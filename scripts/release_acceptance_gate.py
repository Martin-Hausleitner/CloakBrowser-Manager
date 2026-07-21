#!/usr/bin/env python3
"""Compose release evidence into a fail-closed acceptance report.

The gate intentionally publishes only summarized, redacted evidence. Raw local
commands, loopback URLs, private network endpoints, and credential-bearing text
stay out of the generated JSON and Markdown reports.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_HOURS = 24.0


class GateError(RuntimeError):
    """A deterministic release gate failure."""


@dataclass(frozen=True)
class QualityCommand:
    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class RunnerArgs:
    mobile_report: Path
    vision_verdict: Path
    output_json: Path
    output_markdown: Path
    quality_commands: tuple[QualityCommand, ...]
    streaming_report: Path | None
    max_age_hours: float
    timeout: float
    now: datetime


PRIVATE_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)\s*[:=]\s*[^\s,;|]+"),
    re.compile(r"(?i)(authorization:\s*)[^\s,;|]+"),
)


URL_PATTERN = re.compile(r"\b(?:https?|wss?)://[^\s<>)\"']+")
LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![\w:])/(?:Users|home|private|tmp|var/folders|workspace|workspaces|Volumes)/[^\s,;|)]+"),
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\s,;|)]+"),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise GateError(f"{label} is missing a timestamp")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GateError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_fresh(value: dict[str, Any], label: str, now: datetime, max_age_hours: float) -> str:
    timestamp = (
        value.get("finished_at")
        or value.get("generated_at")
        or value.get("created_at")
        or value.get("time")
    )
    parsed = parse_time(timestamp, label)
    max_age = timedelta(hours=max_age_hours)
    if parsed > now + timedelta(minutes=1):
        raise GateError(f"{label} timestamp is in the future")
    age = now - parsed
    if age > max_age:
        raise GateError(f"{label} is stale: {round(age.total_seconds() / 3600, 2)}h old")
    return parsed.isoformat()


def read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise GateError(f"{label} is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise GateError(f"{label} must be a JSON object")
    return value


def is_private_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.lower().strip("[]")
    if host in PRIVATE_HOSTS:
        return True
    if host.endswith(".ts.net"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local):
        return True
    if host.startswith(("10.", "192.168.", "100.")):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def redact_url(match: re.Match[str]) -> str:
    value = match.group(0).rstrip(".,;")
    suffix = match.group(0)[len(value) :]
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted-url]" + suffix
    if parsed.username or parsed.password or parsed.query or is_private_host(parsed.hostname):
        safe_path = parsed.path if parsed.path and parsed.path != "/" else "/"
        if is_private_host(parsed.hostname):
            return "[redacted-local-endpoint]" + suffix
        return urlunsplit((parsed.scheme, parsed.netloc.split("@")[-1], safe_path, "", "")) + suffix
    return value + suffix


def redact_text(value: Any) -> str:
    text = str(value)
    text = URL_PATTERN.sub(redact_url, text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)} [redacted-secret]", text)
    home = str(Path.home())
    if home and home in text:
        text = text.replace(home, "[redacted-home]")
    for pattern in LOCAL_PATH_PATTERNS:
        text = pattern.sub("[redacted-local-path]", text)
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {redact_text(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def parse_quality_command(value: str) -> QualityCommand:
    if "::" not in value:
        raise GateError("--quality-command must use LABEL::COMMAND")
    label, command = value.split("::", 1)
    label = label.strip()
    argv = tuple(shlex.split(command))
    if not label or not argv:
        raise GateError("--quality-command needs a non-empty label and command")
    return QualityCommand(label=label, argv=argv)


def run_quality_command(command: QualityCommand, timeout: float) -> dict[str, Any]:
    completed = subprocess.run(
        list(command.argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    passed = completed.returncode == 0
    evidence: dict[str, Any] = {
        "label": redact_text(command.label),
        "passed": passed,
        "return_code": completed.returncode,
    }
    if not passed:
        tail = "\n".join((completed.stderr or completed.stdout or "").splitlines()[-6:])
        evidence["failure_tail"] = redact_text(tail)
    return evidence


def summarize_quality(commands: tuple[QualityCommand, ...], timeout: float) -> dict[str, Any]:
    if not commands:
        raise GateError("at least one --quality-command is required")
    results = [run_quality_command(command, timeout) for command in commands]
    return {
        "passed": all(result["passed"] for result in results),
        "commands": results,
    }


def summarize_mobile(report: dict[str, Any], now: datetime, max_age_hours: float) -> dict[str, Any]:
    timestamp = ensure_fresh(report, "mobile UI/UX gate report", now, max_age_hours)
    viewports = report.get("viewports")
    if report.get("passed") is not True:
        raise GateError("mobile UI/UX gate did not pass")
    if not isinstance(viewports, list) or not viewports:
        raise GateError("mobile UI/UX gate has no viewport evidence")
    viewport_summaries: list[dict[str, Any]] = []
    total_checks = 0
    total_screenshots = 0
    for item in viewports:
        if not isinstance(item, dict) or item.get("passed") is not True:
            raise GateError("mobile UI/UX gate has a failed viewport")
        checks = item.get("checks")
        screenshots = item.get("screenshots")
        if not isinstance(checks, list) or not checks:
            raise GateError("mobile UI/UX viewport has no checks")
        if not isinstance(screenshots, list) or not screenshots:
            raise GateError("mobile UI/UX viewport has no screenshots")
        total_checks += len(checks)
        total_screenshots += len(screenshots)
        viewport_summaries.append(
            {
                "name": redact_text(item.get("name") or "unknown"),
                "checks": len(checks),
                "screenshots": len(screenshots),
            }
        )
    access_dashboard_required = report.get("access_dashboard_required") is True
    authenticated_run = report.get("authenticated_run") is True
    access_summary: dict[str, Any] | None = None
    if access_dashboard_required:
        if not authenticated_run:
            raise GateError("mobile access dashboard gate was not authenticated")
        access_dashboard = report.get("access_dashboard")
        if not isinstance(access_dashboard, dict) or access_dashboard.get("passed") is not True:
            raise GateError("mobile access dashboard gate did not pass")
        access_checks = access_dashboard.get("checks")
        access_screenshots = access_dashboard.get("screenshots")
        if not isinstance(access_checks, list) or not access_checks:
            raise GateError("mobile access dashboard gate has no checks")
        if not isinstance(access_screenshots, list) or not access_screenshots:
            raise GateError("mobile access dashboard gate has no screenshots")
        total_checks += len(access_checks)
        total_screenshots += len(access_screenshots)
        access_summary = {
            "passed": True,
            "checks": len(access_checks),
            "screenshots": len(access_screenshots),
        }
    return {
        "passed": True,
        "timestamp": timestamp,
        "viewports": viewport_summaries,
        "total_checks": total_checks,
        "total_screenshots": total_screenshots,
        "authenticated_run": authenticated_run,
        "access_dashboard_required": access_dashboard_required,
        "access_dashboard": access_summary,
    }


def vision_passed(report: dict[str, Any]) -> bool:
    if report.get("passed") is True:
        return True
    verdict = str(report.get("verdict") or report.get("status") or "").strip().lower()
    return verdict in {"pass", "passed", "ok", "approved"}


def summarize_vision(report: dict[str, Any], now: datetime, max_age_hours: float) -> dict[str, Any]:
    timestamp = ensure_fresh(report, "vision verdict", now, max_age_hours)
    if not vision_passed(report):
        raise GateError("vision verdict did not pass")
    summary = report.get("summary") or report.get("notes") or report.get("label") or "passed"
    return {
        "passed": True,
        "timestamp": timestamp,
        "verdict": redact_text(report.get("verdict") or report.get("status") or "passed"),
        "summary": redact_text(summary),
    }


def summarize_streaming(report: dict[str, Any], now: datetime, max_age_hours: float) -> dict[str, Any]:
    timestamp = ensure_fresh(report, "streaming benchmark report", now, max_age_hours)
    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise GateError("streaming benchmark report has no candidates")
    candidate_summaries: list[dict[str, Any]] = []
    measured = 0
    failed = 0
    for item in results:
        if not isinstance(item, dict):
            raise GateError("streaming benchmark candidate is invalid")
        status = str(item.get("status") or "")
        availability = str(item.get("availability") or "")
        if availability == "error":
            failed += 1
        if status == "measured":
            measured += 1
            if availability != "available":
                failed += 1
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        candidate_summaries.append(
            {
                "id": redact_text(candidate.get("id") or "unknown"),
                "status": redact_text(status),
                "availability": redact_text(availability),
                "runs": (item.get("summary") or {}).get("runs", 0),
            }
        )
    if measured < 1:
        raise GateError("streaming benchmark has no measured candidates")
    if failed:
        raise GateError("streaming benchmark has failed measured candidates")
    return {
        "passed": True,
        "timestamp": timestamp,
        "measured_candidates": measured,
        "candidates": candidate_summaries,
    }


def render_markdown(report: dict[str, Any]) -> str:
    status = "PASS" if report["passed"] else "FAIL"
    lines = [
        "# Release Acceptance Gate",
        "",
        f"Status: `{status}`",
        f"Generated: `{report['generated_at']}`",
        "",
        "This public report is redacted: local endpoints, credential-bearing text, and local paths are not emitted.",
        "",
        "| Gate | Status | Evidence |",
        "|---|---|---|",
    ]
    for name, gate in report["gates"].items():
        gate_status = "PASS" if gate.get("passed") else "FAIL"
        if name == "quality":
            evidence = ", ".join(
                f"{item['label']} rc={item['return_code']}" for item in gate.get("commands", [])
            )
        elif name == "mobile_ui_ux":
            evidence = (
                f"{gate.get('total_checks', 0)} checks, "
                f"{gate.get('total_screenshots', 0)} screenshots, "
                f"{len(gate.get('viewports', []))} viewports"
            )
        elif name == "vision":
            evidence = str(gate.get("summary") or gate.get("verdict") or "")
        elif name == "streaming":
            evidence = f"{gate.get('measured_candidates', 0)} measured candidates"
        else:
            evidence = str(gate)
        lines.append(f"| `{name}` | `{gate_status}` | {redact_text(evidence).replace('|', '\\|')} |")
    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in report["failures"]:
            lines.append(f"- {redact_text(failure)}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], output_json: Path, output_markdown: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    public_report = redact_value(report)
    output_json.write_text(
        json.dumps(public_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_markdown.write_text(render_markdown(public_report), encoding="utf-8")


def build_report(args: RunnerArgs) -> tuple[int, dict[str, Any]]:
    generated_at = args.now.isoformat()
    gates: dict[str, Any] = {}
    failures: list[str] = []

    try:
        gates["quality"] = summarize_quality(args.quality_commands, args.timeout)
        if not gates["quality"]["passed"]:
            failures.append("quality command failed")
    except Exception as exc:
        gates["quality"] = {"passed": False}
        failures.append(redact_text(str(exc)))

    for name, path, summarizer in (
        ("mobile_ui_ux", args.mobile_report, summarize_mobile),
        ("vision", args.vision_verdict, summarize_vision),
    ):
        try:
            gates[name] = summarizer(read_json(path, name), args.now, args.max_age_hours)
        except Exception as exc:
            gates[name] = {"passed": False}
            failures.append(redact_text(str(exc)))

    if args.streaming_report is not None:
        try:
            gates["streaming"] = summarize_streaming(
                read_json(args.streaming_report, "streaming"),
                args.now,
                args.max_age_hours,
            )
        except Exception as exc:
            gates["streaming"] = {"passed": False}
            failures.append(redact_text(str(exc)))
    else:
        gates["streaming"] = {"passed": True, "optional": True, "status": "not_requested"}

    passed = all(bool(gate.get("passed")) for gate in gates.values()) and not failures
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "passed": passed,
        "max_age_hours": args.max_age_hours,
        "gates": gates,
        "failures": failures,
    }
    return (0 if passed else 1), report


def parse_args(argv: list[str] | None = None) -> RunnerArgs:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile-report", type=Path, required=True)
    parser.add_argument("--vision-verdict", type=Path, required=True)
    parser.add_argument("--streaming-report", type=Path)
    parser.add_argument("--quality-command", action="append", default=[])
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--now",
        help="UTC timestamp override for deterministic tests.",
    )
    parsed = parser.parse_args(argv)
    if parsed.max_age_hours <= 0:
        raise GateError("--max-age-hours must be positive")
    if parsed.timeout <= 0:
        raise GateError("--timeout must be positive")
    now = parse_time(parsed.now, "--now") if parsed.now else utc_now()
    commands = tuple(parse_quality_command(item) for item in parsed.quality_command)
    return RunnerArgs(
        mobile_report=parsed.mobile_report,
        vision_verdict=parsed.vision_verdict,
        output_json=parsed.output_json,
        output_markdown=parsed.output_markdown,
        quality_commands=commands,
        streaming_report=parsed.streaming_report,
        max_age_hours=parsed.max_age_hours,
        timeout=parsed.timeout,
        now=now,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        code, report = build_report(args)
        write_outputs(report, args.output_json, args.output_markdown)
        print(json.dumps({"passed": report["passed"], "output_json": args.output_json.name}))
        return code
    except GateError as exc:
        print(f"release gate configuration error: {redact_text(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
