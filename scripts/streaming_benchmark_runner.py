#!/usr/bin/env python3
"""Run reproducible streaming candidate speed checks.

The runner intentionally measures only what it can observe locally. Candidates
that are not installed, are architecture-only, or are intentionally skipped are
reported as such and never receive synthetic timing values.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import math
import os
import re
import selectors
import shlex
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_SAMPLE_BYTES = 262_144


@dataclass(frozen=True)
class RunnerArgs:
    config: Path
    output_dir: Path
    iterations: int
    timeout: float
    markdown: Path | None
    latest_json: Path | None
    latest_markdown: Path | None
    strict: bool


class ConfigError(ValueError):
    pass


class CandidateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def monotonic_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the candidate metadata that is safe for a browser-visible report.

    Candidate configurations can contain private loopback URLs, profile IDs,
    disposable authentication headers, shell commands, or arbitrary notes. The
    generated JSON is designed to be served by the manager UI, so keep that
    operational material out of it by default.
    """
    safe_keys = {
        "id",
        "name",
        "type",
        "architecture_only",
    }
    public = {key: candidate[key] for key in safe_keys if key in candidate}
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        safe_metadata_keys = {"technology", "version", "comparison_role"}
        safe_metadata = {
            key: value
            for key, value in metadata.items()
            if key in safe_metadata_keys
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        if safe_metadata:
            public["metadata"] = safe_metadata
    return public


def emit_event(event: dict[str, Any]) -> None:
    payload = {"schema_version": SCHEMA_VERSION, "time": utc_now(), **event}
    print(json.dumps(payload, sort_keys=True), flush=True)


def read_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    candidates = config.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ConfigError("config must contain a non-empty candidates array")
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ConfigError(f"candidate #{index + 1} must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ConfigError(f"candidate #{index + 1} needs a non-empty id")
        if candidate_id in seen:
            raise ConfigError(f"duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)
        candidate_type = candidate.get("type")
        if candidate_type not in {"http", "websocket", "command", "architecture"}:
            raise ConfigError(
                f"{candidate_id}: type must be http, websocket, command, or architecture"
            )
    return config


def command_to_args(command: Any) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return command
    raise ConfigError("command candidates need command as a string or array of strings")


def candidate_skip_reason(candidate: dict[str, Any]) -> tuple[str, str] | None:
    if candidate.get("architecture_only") or candidate.get("type") == "architecture":
        return ("architecture_only", str(candidate.get("architecture_note") or "not measured"))
    required = candidate.get("requires_executable")
    if required:
        if not isinstance(required, str):
            raise ConfigError(f"{candidate['id']}: requires_executable must be a string")
        if shutil.which(required) is None:
            return ("not_installed", f"required executable not found on PATH: {required}")
    return None


def open_socket(parsed: Any, timeout: float) -> tuple[socket.socket, dict[str, float]]:
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise CandidateError(f"unsupported URL scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise CandidateError("URL is missing a host")
    default_port = 443 if parsed.scheme in {"https", "wss"} else 80
    port = parsed.port or default_port
    timings: dict[str, float] = {}
    start = time.perf_counter()
    sock = socket.create_connection((host, port), timeout=timeout)
    timings["connect_ms"] = monotonic_ms(start)
    if parsed.scheme in {"https", "wss"}:
        tls_start = time.perf_counter()
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=host)
        timings["tls_ms"] = monotonic_ms(tls_start)
    sock.settimeout(timeout)
    return sock, timings


def target_path(parsed: Any) -> str:
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def candidate_headers(candidate: dict[str, Any]) -> dict[str, str]:
    headers = candidate.get("headers") or {}
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise ConfigError(f"{candidate['id']}: headers must be an object of string values")
    return headers


def render_extra_headers(headers: dict[str, str]) -> str:
    if not headers:
        return ""
    return "".join(f"{key}: {value}\r\n" for key, value in headers.items())


def measure_http(candidate: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = candidate.get("url")
    if not isinstance(url, str) or not url:
        raise ConfigError(f"{candidate['id']}: http candidate needs url")
    parsed = urlparse(url)
    sock, timings = open_socket(parsed, timeout)
    request_start = time.perf_counter()
    try:
        path = target_path(parsed)
        host = parsed.netloc
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: cloak-streaming-benchmark/1\r\n"
            "Accept: */*\r\n"
            f"{render_extra_headers(candidate_headers(candidate))}"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = http.client.HTTPResponse(sock)
        response.begin()
        timings["first_byte_ms"] = monotonic_ms(request_start)
        body = response.read(MAX_SAMPLE_BYTES)
        total_ms = monotonic_ms(request_start)
        return {
            "available": 200 <= response.status < 500,
            "status_code": response.status,
            "reason": response.reason,
            "bytes_sampled": len(body),
            "timings_ms": {**timings, "total_ms": total_ms},
        }
    finally:
        sock.close()


def websocket_key(candidate_id: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}:{time.time_ns()}".encode("utf-8")).digest()
    return base64.b64encode(digest[:16]).decode("ascii")


def measure_websocket(candidate: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = candidate.get("url")
    if not isinstance(url, str) or not url:
        raise ConfigError(f"{candidate['id']}: websocket candidate needs url")
    parsed = urlparse(url)
    sock, timings = open_socket(parsed, timeout)
    request_start = time.perf_counter()
    try:
        key = websocket_key(candidate["id"])
        path = target_path(parsed)
        host = parsed.netloc
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: cloak-streaming-benchmark/1\r\n\r\n"
        )
        extra_headers = render_extra_headers(candidate_headers(candidate))
        request = request.replace("\r\n\r\n", f"\r\n{extra_headers}\r\n").encode("ascii")
        sock.sendall(request)
        chunks: list[bytes] = []
        while b"\r\n\r\n" not in b"".join(chunks):
            chunk = sock.recv(4096)
            if not chunk:
                break
            if not chunks:
                timings["first_byte_ms"] = monotonic_ms(request_start)
            chunks.append(chunk)
            if sum(len(item) for item in chunks) > 65_536:
                break
        header = b"".join(chunks).decode("iso-8859-1", errors="replace")
        status_line = header.splitlines()[0] if header.splitlines() else ""
        match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d+)", status_line)
        status_code = int(match.group(1)) if match else None
        return {
            "available": status_code == 101,
            "status_code": status_code,
            "status_line": status_line,
            "bytes_sampled": sum(len(item) for item in chunks),
            "timings_ms": {**timings, "handshake_ms": monotonic_ms(request_start)},
        }
    finally:
        sock.close()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def read_process_stream(
    process: subprocess.Popen[bytes],
    timeout: float,
    ready_regex: str | None,
) -> dict[str, Any]:
    """Collect process readiness without publishing stdout or stderr.

    A command candidate often starts a long-running local service. Read both
    streams incrementally so a matching readiness line is timed accurately,
    then terminate the local probe rather than waiting until its timeout.
    """
    start = time.perf_counter()
    first_stdout_ms: float | None = None
    first_stderr_ms: float | None = None
    ready_ms: float | None = None
    ready_pattern = re.compile(ready_regex) if ready_regex else None
    deadline = start + timeout
    timed_out = False
    stream_tail: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

    selector = selectors.DefaultSelector()
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")

    try:
        while selector.get_map():
            now = time.perf_counter()
            if now >= deadline:
                timed_out = process.poll() is None
                break

            events = selector.select(timeout=min(0.05, max(0.0, deadline - now)))
            if not events:
                if process.poll() is not None:
                    break
                continue

            for key, _ in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue

                stream_name = str(key.data)
                if stream_name == "stdout" and first_stdout_ms is None:
                    first_stdout_ms = monotonic_ms(start)
                if stream_name == "stderr" and first_stderr_ms is None:
                    first_stderr_ms = monotonic_ms(start)

                tail = stream_tail[stream_name]
                tail.extend(chunk)
                if len(tail) > 16_384:
                    del tail[:-16_384]

                if ready_pattern and ready_ms is None:
                    combined = (stream_tail["stdout"] + b"\n" + stream_tail["stderr"]).decode(
                        "utf-8", errors="replace"
                    )
                    if ready_pattern.search(combined):
                        ready_ms = monotonic_ms(start)

            if ready_ms is not None and process.poll() is None:
                _terminate_process(process)
                break
    finally:
        selector.close()

    if process.poll() is None:
        timed_out = True
        _terminate_process(process)

    return {
        "return_code": process.returncode,
        "ready": ready_ms is not None,
        "timed_out": timed_out,
        "timings_ms": {
            "exit_ms": monotonic_ms(start),
            **({"first_stdout_ms": first_stdout_ms} if first_stdout_ms is not None else {}),
            **({"first_stderr_ms": first_stderr_ms} if first_stderr_ms is not None else {}),
            **({"ready_ms": ready_ms} if ready_ms is not None else {}),
        },
    }


def measure_command(candidate: dict[str, Any], timeout: float) -> dict[str, Any]:
    command = command_to_args(candidate.get("command"))
    ready_regex = candidate.get("ready_regex")
    if ready_regex is not None and not isinstance(ready_regex, str):
        raise ConfigError(f"{candidate['id']}: ready_regex must be a string")
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process_start_ms = monotonic_ms(started)
    result = read_process_stream(process, timeout, ready_regex)
    result["available"] = bool(result["ready"]) if ready_regex else (
        not result["timed_out"] and result["return_code"] == 0
    )
    result["timings_ms"] = {"process_start_ms": process_start_ms, **result["timings_ms"]}
    return result


def run_one(candidate: dict[str, Any], timeout: float) -> dict[str, Any]:
    skip = candidate_skip_reason(candidate)
    if skip:
        status, reason = skip
        return {
            "candidate": public_candidate(candidate),
            "status": status,
            "availability": "not_measured",
            "reason": reason,
            "measurements": [],
        }
    candidate_type = candidate["type"]
    try:
        if candidate_type == "http":
            measurement = measure_http(candidate, timeout)
        elif candidate_type == "websocket":
            measurement = measure_websocket(candidate, timeout)
        elif candidate_type == "command":
            measurement = measure_command(candidate, timeout)
        else:
            raise ConfigError(f"{candidate['id']}: unsupported measurable type {candidate_type}")
        return {
            "candidate": public_candidate(candidate),
            "status": "measured",
            "availability": "available" if measurement.get("available") else "unavailable",
            "measurements": [measurement],
        }
    except Exception as exc:
        return {
            "candidate": public_candidate(candidate),
            "status": "measured",
            "availability": "error",
            "measurements": [],
            "error_kind": type(exc).__name__,
        }


def merge_iteration_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    base = dict(results[0])
    measurements: list[dict[str, Any]] = []
    error_kinds: list[str] = []
    for result in results:
        measurements.extend(result.get("measurements", []))
        if result.get("error_kind"):
            error_kinds.append(str(result["error_kind"]))
    base["measurements"] = measurements
    if error_kinds:
        base["error_kinds"] = sorted(set(error_kinds))
        base["availability"] = "error"
    elif measurements:
        base["availability"] = (
            "available" if any(item.get("available") for item in measurements) else "unavailable"
        )
    base["summary"] = summarize_measurements(measurements)
    return base


def percentile(values: list[float], percent: float) -> float:
    """Return the nearest-rank percentile for a non-empty list."""
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percent / 100.0) - 1)
    return ordered[index]


def summarize_measurements(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    timing_names = sorted(
        {
            name
            for measurement in measurements
            for name, value in (measurement.get("timings_ms") or {}).items()
            if isinstance(value, (int, float))
        }
    )
    summary: dict[str, Any] = {"runs": len(measurements)}
    timings: dict[str, Any] = {}
    for name in timing_names:
        values = [
            float(measurement["timings_ms"][name])
            for measurement in measurements
            if isinstance((measurement.get("timings_ms") or {}).get(name), (int, float))
        ]
        if values:
            timings[name] = {
                "min": round(min(values), 3),
                "median": round(statistics.median(values), 3),
                "p95": round(percentile(values, 95), 3),
                "max": round(max(values), 3),
            }
    if timings:
        summary["timings_ms"] = timings
    sampled = [
        int(measurement["bytes_sampled"])
        for measurement in measurements
        if isinstance(measurement.get("bytes_sampled"), int)
    ]
    if sampled:
        summary["bytes_sampled"] = {
            "min": min(sampled),
            "median": round(statistics.median(sampled), 3),
            "max": max(sampled),
        }
    if measurements:
        summary["success_rate_pct"] = round(
            100.0 * sum(bool(measurement.get("available")) for measurement in measurements) / len(measurements),
            2,
        )
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Streaming Speed Benchmark Report",
        "",
        f"Generated: `{report['finished_at']}`",
        "",
        "This report contains only locally observed measurements. Entries marked "
        "`not_installed` or `architecture_only` were not benchmarked.",
        "",
        "| Candidate | Type | Status | Availability | Key timings | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for result in report["results"]:
        candidate = result["candidate"]
        summary = result.get("summary") or {}
        timings = summary.get("timings_ms") or {}
        timing_bits = []
        for name in ("connect_ms", "tls_ms", "first_byte_ms", "handshake_ms", "total_ms", "exit_ms"):
            if name in timings:
                timing_bits.append(
                    f"{name} median {timings[name]['median']} ms (p95 {timings[name].get('p95', timings[name]['max'])} ms)"
                )
        notes = result.get("reason") or result.get("error_kind") or ""
        lines.append(
            "| {name} | `{type}` | `{status}` | `{availability}` | {timings} | {notes} |".format(
                name=str(candidate.get("name") or candidate["id"]).replace("|", "\\|"),
                type=candidate.get("type", ""),
                status=result.get("status", ""),
                availability=result.get("availability", ""),
                timings="<br>".join(timing_bits) if timing_bits else "-",
                notes=str(notes).replace("|", "\\|") if notes else "-",
            )
        )
    lines.extend(
        [
            "",
            "The browser-facing report intentionally omits local paths, commands, endpoints, and credential-bearing headers.",
            "See `docs/STREAMING-SPEED-TEST-RUNNER.md` for the reproducible command shape.",
            "",
        ]
    )
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(args: RunnerArgs) -> int:
    config = read_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "streaming-benchmark-report.json"
    markdown_path = args.markdown or args.output_dir / "streaming-benchmark-report.md"
    started_at = utc_now()
    emit_event({"event": "run_started", "iterations": args.iterations})
    results: list[dict[str, Any]] = []
    for candidate in config["candidates"]:
        candidate_id = candidate["id"]
        emit_event({"event": "candidate_started", "candidate_id": candidate_id})
        iteration_results = []
        iterations = 1 if candidate_skip_reason(candidate) else args.iterations
        for iteration in range(iterations):
            emit_event(
                {
                    "event": "iteration_started",
                    "candidate_id": candidate_id,
                    "iteration": iteration + 1,
                }
            )
            result = run_one(candidate, float(candidate.get("timeout", args.timeout)))
            iteration_results.append(result)
            emit_event(
                {
                    "event": "iteration_finished",
                    "candidate_id": candidate_id,
                    "iteration": iteration + 1,
                    "status": result.get("status"),
                    "availability": result.get("availability"),
                    "error_kind": result.get("error_kind"),
                }
            )
        merged = merge_iteration_results(iteration_results)
        results.append(merged)
        emit_event(
            {
                "event": "candidate_finished",
                "candidate_id": candidate_id,
                "status": merged.get("status"),
                "availability": merged.get("availability"),
            }
        )
    finished_at = utc_now()
    report = {
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "config": {
            "iterations": args.iterations,
            "timeout_seconds": args.timeout,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "results": results,
    }
    markdown = render_markdown(report)
    write_json(report_path, report)
    write_text(markdown_path, markdown)
    if args.latest_json:
        write_json(args.latest_json, report)
    if args.latest_markdown:
        write_text(args.latest_markdown, markdown)
    emit_event(
        {
            "event": "run_finished",
            "report_json": report_path.name,
            "report_markdown": markdown_path.name,
        }
    )
    has_errors = any(result.get("availability") == "error" for result in results)
    return 1 if args.strict and has_errors else 0


def parse_args(argv: list[str] | None = None) -> RunnerArgs:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON candidate config.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/streaming-benchmark"),
        help="Directory for JSON and Markdown reports.",
    )
    parser.add_argument("--iterations", type=int, default=3, help="Measured runs per candidate.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--markdown", type=Path, help="Optional Markdown report path.")
    parser.add_argument("--latest-json", type=Path, help="Also copy JSON report to this path.")
    parser.add_argument(
        "--latest-markdown",
        type=Path,
        help="Also copy Markdown report to this path, for README/doc adapters.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when a measurable candidate errors.",
    )
    parsed = parser.parse_args(argv)
    if parsed.iterations < 1:
        raise ConfigError("--iterations must be at least 1")
    if parsed.timeout <= 0:
        raise ConfigError("--timeout must be positive")
    return RunnerArgs(
        config=parsed.config,
        output_dir=parsed.output_dir,
        iterations=parsed.iterations,
        timeout=parsed.timeout,
        markdown=parsed.markdown,
        latest_json=parsed.latest_json,
        latest_markdown=parsed.latest_markdown,
        strict=parsed.strict,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
