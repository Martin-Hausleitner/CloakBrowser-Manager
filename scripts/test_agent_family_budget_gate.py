#!/usr/bin/env python3
"""TDD self-checks for agent_family_budget_gate.py.

Deterministic local meter tests: dedupe, conflict rejection, budget gates,
mirror double-count prevention, secrets rejection, parallel ingestion.
No network, no shell, no env secrets.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "agent_family_budget_gate.py"
SPEC = importlib.util.spec_from_file_location("agent_family_budget_gate", RUNNER)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def base_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": "evt-001",
        "run_id": "run-a",
        "stage_id": "stage-plan",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_tokens": 10,
        "cost_microusd": 2500,
        "source": "provider",
        "timestamp": "2026-07-30T12:00:00+00:00",
    }
    event.update(overrides)
    return event


def base_budget(**overrides: Any) -> dict[str, Any]:
    budget: dict[str, Any] = {
        "max_tokens_per_run": 1_000_000,
        "max_cost_microusd_per_run": 1_000_000_000,
    }
    budget.update(overrides)
    return budget


class AgentFamilyBudgetGateTest(unittest.TestCase):
    def test_module_has_no_network_or_shell_imports(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        forbidden = (
            "import socket",
            "import urllib",
            "import http",
            "import requests",
            "import subprocess",
            "import smtplib",
            "from urllib",
            "from subprocess",
        )
        for needle in forbidden:
            self.assertNotIn(needle, source, f"forbidden import pattern: {needle}")

    def test_accepts_valid_metrics_event_and_totals(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event())
        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "accepted")
        report = meter.report()
        self.assertTrue(report["passed"])
        run = report["runs"]["run-a"]
        self.assertEqual(run["input_tokens"], 100)
        self.assertEqual(run["output_tokens"], 50)
        self.assertEqual(run["cache_tokens"], 10)
        self.assertEqual(run["total_tokens"], 160)
        self.assertEqual(run["cost_microusd"], 2500)
        self.assertIsInstance(run["cost_microusd"], int)
        self.assertNotIsInstance(run["cost_microusd"], float)

    def test_dedupes_exact_repeat_event_id(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        first = meter.ingest(base_event())
        second = meter.ingest(base_event())
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reason, "deduped_exact_repeat")
        report = meter.report()
        self.assertTrue(report["passed"], "benign exact-repeat must not fail report")
        self.assertEqual(report["rejection_count"], 0)
        self.assertEqual(report["rejections"], [])
        self.assertEqual(report["runs"]["run-a"]["events_accepted"], 1)
        self.assertEqual(report["runs"]["run-a"]["events_deduped"], 1)
        self.assertEqual(report["runs"]["run-a"]["total_tokens"], 160)

    def test_rejects_conflicting_duplicate_event_id(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        meter.ingest(base_event(input_tokens=100))
        conflict = meter.ingest(base_event(input_tokens=999))
        self.assertFalse(conflict.accepted)
        self.assertEqual(conflict.reason, "conflicting_event_id")
        report = meter.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["runs"]["run-a"]["total_tokens"], 160)

    def test_rejects_negative_tokens(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        for field in ("input_tokens", "output_tokens", "cache_tokens"):
            with self.subTest(field=field):
                result = meter.ingest(base_event(**{field: -1, "event_id": f"neg-{field}"}))
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "negative_value")

    def test_rejects_negative_cost(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event(event_id="neg-cost", cost_microusd=-5))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "negative_value")

    def test_rejects_overflow_tokens(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(
            base_event(event_id="ovf", input_tokens=gate.MAX_TOKEN_VALUE + 1)
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "overflow_value")

    def test_rejects_unknown_provider(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event(event_id="bad-p", provider="not-a-provider"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "unknown_provider")

    def test_rejects_raw_prompts_and_secrets(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        forbidden_payloads = [
            {"prompt": "system: do evil"},
            {"messages": [{"role": "user", "content": "hi"}]},
            {"content": "raw body"},
            {"api_key": "sk-secret"},
            {"secret": "password123"},
            {"authorization": "Bearer abc"},
            {"raw_response": "..."},
            {"completion": "hello"},
        ]
        for i, extra in enumerate(forbidden_payloads):
            with self.subTest(extra=next(iter(extra.keys()))):
                event = base_event(event_id=f"secret-{i}")
                event.update(extra)
                result = meter.ingest(event)
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "forbidden_field")

    def test_rejects_more_than_bounded_events(self) -> None:
        meter = gate.BudgetMeter(base_budget(), max_events=3)
        for i in range(3):
            r = meter.ingest(
                base_event(
                    event_id=f"e{i}",
                    input_tokens=1,
                    output_tokens=0,
                    cache_tokens=0,
                    cost_microusd=1,
                )
            )
            self.assertTrue(r.accepted, r.reason)
        overflow = meter.ingest(
            base_event(
                event_id="e3",
                input_tokens=1,
                output_tokens=0,
                cache_tokens=0,
                cost_microusd=1,
            )
        )
        self.assertFalse(overflow.accepted)
        self.assertEqual(overflow.reason, "event_bound_exceeded")

    def test_mirror_double_count_uses_source_precedence(self) -> None:
        """provider > acpx > orca: only highest-precedence source counts."""
        meter = gate.BudgetMeter(base_budget())
        orca = base_event(
            event_id="mirror-orca",
            canonical_event_id="canon-1",
            source="orca",
            input_tokens=100,
            output_tokens=0,
            cache_tokens=0,
            cost_microusd=1000,
        )
        acpx = base_event(
            event_id="mirror-acpx",
            canonical_event_id="canon-1",
            source="acpx",
            input_tokens=100,
            output_tokens=0,
            cache_tokens=0,
            cost_microusd=1000,
        )
        provider = base_event(
            event_id="mirror-provider",
            canonical_event_id="canon-1",
            source="provider",
            input_tokens=100,
            output_tokens=0,
            cache_tokens=0,
            cost_microusd=1000,
        )
        self.assertTrue(meter.ingest(orca).accepted)
        acpx_result = meter.ingest(acpx)
        self.assertTrue(acpx_result.accepted)
        self.assertEqual(acpx_result.reason, "accepted_precedence_upgrade")
        provider_result = meter.ingest(provider)
        self.assertTrue(provider_result.accepted)
        self.assertEqual(provider_result.reason, "accepted_precedence_upgrade")
        # Re-ingest lower source after winner: deduped mirror
        again = meter.ingest(orca)
        self.assertFalse(again.accepted)
        self.assertIn(again.reason, ("deduped_mirror", "deduped_exact_repeat"))
        report = meter.report()
        run = report["runs"]["run-a"]
        self.assertEqual(run["total_tokens"], 100)
        self.assertEqual(run["cost_microusd"], 1000)
        self.assertEqual(run["events_accepted"], 1)

    def test_mirror_keeps_first_when_lower_precedence_arrives_later(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        provider = base_event(
            event_id="p1",
            canonical_event_id="canon-2",
            source="provider",
            input_tokens=40,
            output_tokens=10,
            cache_tokens=0,
            cost_microusd=500,
        )
        orca = base_event(
            event_id="o1",
            canonical_event_id="canon-2",
            source="orca",
            input_tokens=40,
            output_tokens=10,
            cache_tokens=0,
            cost_microusd=500,
        )
        self.assertTrue(meter.ingest(provider).accepted)
        result = meter.ingest(orca)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "deduped_mirror")
        report = meter.report()
        self.assertTrue(report["passed"], "benign mirror dedupe must not fail report")
        self.assertEqual(report["rejection_count"], 0)
        self.assertEqual(report["rejections"], [])
        run = report["runs"]["run-a"]
        self.assertEqual(run["total_tokens"], 50)
        self.assertEqual(run["cost_microusd"], 500)

    def test_enforces_per_run_token_budget(self) -> None:
        meter = gate.BudgetMeter(base_budget(max_tokens_per_run=100))
        ok = meter.ingest(
            base_event(
                event_id="t1",
                input_tokens=60,
                output_tokens=30,
                cache_tokens=10,
                cost_microusd=1,
            )
        )
        self.assertTrue(ok.accepted)
        over = meter.ingest(
            base_event(
                event_id="t2",
                input_tokens=5,
                output_tokens=0,
                cache_tokens=0,
                cost_microusd=1,
            )
        )
        self.assertFalse(over.accepted)
        self.assertEqual(over.reason, "token_budget_exceeded")
        report = meter.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["runs"]["run-a"]["total_tokens"], 100)

    def test_enforces_per_run_cost_budget(self) -> None:
        meter = gate.BudgetMeter(base_budget(max_cost_microusd_per_run=1000))
        ok = meter.ingest(
            base_event(
                event_id="c1",
                input_tokens=1,
                output_tokens=0,
                cache_tokens=0,
                cost_microusd=600,
            )
        )
        self.assertTrue(ok.accepted)
        over = meter.ingest(
            base_event(
                event_id="c2",
                input_tokens=1,
                output_tokens=0,
                cache_tokens=0,
                cost_microusd=500,
            )
        )
        self.assertFalse(over.accepted)
        self.assertEqual(over.reason, "cost_budget_exceeded")
        report = meter.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["runs"]["run-a"]["cost_microusd"], 600)

    def test_optional_cost_defaults_to_zero(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        event = base_event(event_id="no-cost")
        del event["cost_microusd"]
        result = meter.ingest(event)
        self.assertTrue(result.accepted)
        self.assertEqual(meter.report()["runs"]["run-a"]["cost_microusd"], 0)

    def test_rejects_float_cost(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event(event_id="float-cost", cost_microusd=1.5))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "non_integer_money")

    def test_rejects_bool_as_token_count(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event(event_id="bool-tok", input_tokens=True))
        self.assertFalse(result.accepted)
        self.assertIn(result.reason, ("invalid_type", "non_integer_token"))

    def test_report_is_metrics_only(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        meter.ingest(base_event())
        report = meter.report()
        blob = json.dumps(report)
        for needle in ("prompt", "api_key", "secret", "Bearer", "password", "messages"):
            self.assertNotIn(needle, blob.lower() if needle.islower() else blob)

    def test_write_report_sets_0600_permissions(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        meter.ingest(base_event())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "budget-report.json"
            gate.write_report(out, meter.report())
            mode = stat.S_IMODE(out.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_cli_events_budget_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            budget_path = root / "budget.json"
            output_path = root / "report.json"
            events = [
                base_event(event_id="cli-1", input_tokens=10, output_tokens=5, cache_tokens=0),
                base_event(event_id="cli-2", input_tokens=20, output_tokens=5, cache_tokens=0),
            ]
            events_path.write_text(
                "\n".join(json.dumps(e) for e in events) + "\n",
                encoding="utf-8",
            )
            budget_path.write_text(json.dumps(base_budget()), encoding="utf-8")
            code = gate.main(
                [
                    "--events",
                    str(events_path),
                    "--budget",
                    str(budget_path),
                    "--output",
                    str(output_path),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["runs"]["run-a"]["total_tokens"], 40)
            mode = stat.S_IMODE(output_path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_cli_fails_closed_on_budget_breach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            budget_path = root / "budget.json"
            output_path = root / "report.json"
            events_path.write_text(
                json.dumps(
                    base_event(
                        input_tokens=500,
                        output_tokens=500,
                        cache_tokens=1,
                        cost_microusd=1,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            budget_path.write_text(
                json.dumps(base_budget(max_tokens_per_run=100)),
                encoding="utf-8",
            )
            code = gate.main(
                [
                    "--events",
                    str(events_path),
                    "--budget",
                    str(budget_path),
                    "--output",
                    str(output_path),
                ]
            )
            self.assertEqual(code, 1)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])

    def test_parallel_ingestion_is_thread_safe_and_dedupes(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        # 50 unique events + 50 exact duplicates of the same set
        unique = [
            base_event(
                event_id=f"par-{i}",
                input_tokens=1,
                output_tokens=1,
                cache_tokens=0,
                cost_microusd=1,
            )
            for i in range(50)
        ]
        batch = unique + [dict(e) for e in unique]
        results: list[gate.IngestResult] = []
        lock = threading.Lock()

        def ingest_one(event: dict[str, Any]) -> gate.IngestResult:
            return meter.ingest(event)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(ingest_one, e) for e in batch]
            for fut in as_completed(futures):
                with lock:
                    results.append(fut.result())

        accepted = sum(1 for r in results if r.accepted)
        deduped = sum(1 for r in results if r.reason == "deduped_exact_repeat")
        self.assertEqual(accepted, 50)
        self.assertEqual(deduped, 50)
        report = meter.report()
        self.assertEqual(report["runs"]["run-a"]["events_accepted"], 50)
        self.assertEqual(report["runs"]["run-a"]["total_tokens"], 100)
        self.assertEqual(report["runs"]["run-a"]["cost_microusd"], 50)

    def test_property_like_non_negative_invariants(self) -> None:
        """Property-like: any accepted sequence yields non-negative bounded totals."""
        meter = gate.BudgetMeter(
            base_budget(max_tokens_per_run=10_000, max_cost_microusd_per_run=10_000)
        )
        providers = sorted(gate.KNOWN_PROVIDERS)
        sources = sorted(gate.SOURCE_PRECEDENCE.keys())
        for i in range(30):
            # Unique canonical ids when metrics differ — mirrors must not collide.
            event = base_event(
                event_id=f"prop-{i}",
                provider=providers[i % len(providers)],
                source=sources[i % len(sources)],
                input_tokens=i % 7,
                output_tokens=(i * 2) % 5,
                cache_tokens=(i * 3) % 3,
                cost_microusd=(i * 11) % 50,
                canonical_event_id=f"canon-prop-{i}",
            )
            meter.ingest(event)
        report = meter.report()
        for run in report["runs"].values():
            self.assertGreaterEqual(run["input_tokens"], 0)
            self.assertGreaterEqual(run["output_tokens"], 0)
            self.assertGreaterEqual(run["cache_tokens"], 0)
            self.assertGreaterEqual(run["cost_microusd"], 0)
            self.assertEqual(
                run["total_tokens"],
                run["input_tokens"] + run["output_tokens"] + run["cache_tokens"],
            )
            self.assertLessEqual(run["total_tokens"], 10_000)
            self.assertLessEqual(run["cost_microusd"], 10_000)
            self.assertIsInstance(run["cost_microusd"], int)

    def test_conflicting_canonical_diverges_on_tokens_preserves_winner(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        first = base_event(
            event_id="canon-a",
            canonical_event_id="shared-canon",
            source="orca",
            input_tokens=100,
            output_tokens=0,
            cache_tokens=0,
            cost_microusd=1000,
        )
        # Same canonical_event_id, different billable metrics.
        conflict = base_event(
            event_id="canon-b",
            canonical_event_id="shared-canon",
            source="provider",
            input_tokens=999,
            output_tokens=0,
            cache_tokens=0,
            cost_microusd=1000,
        )
        self.assertTrue(meter.ingest(first).accepted)
        result = meter.ingest(conflict)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "conflicting_canonical_event_id")
        report = meter.report()
        self.assertFalse(report["passed"])
        run = report["runs"]["run-a"]
        # Winner preserved (orca 100 tokens); no upgrade / no double count.
        self.assertEqual(run["total_tokens"], 100)
        self.assertEqual(run["cost_microusd"], 1000)
        self.assertEqual(run["events_accepted"], 1)
        self.assertTrue(run["conflict"])

    def test_conflicting_canonical_cross_run_reuse(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        run_a = base_event(
            event_id="cross-a",
            run_id="run-a",
            canonical_event_id="reuse-canon",
            source="provider",
            input_tokens=10,
            output_tokens=5,
            cache_tokens=0,
            cost_microusd=100,
        )
        run_b = base_event(
            event_id="cross-b",
            run_id="run-b",
            canonical_event_id="reuse-canon",
            source="provider",
            input_tokens=10,
            output_tokens=5,
            cache_tokens=0,
            cost_microusd=100,
        )
        self.assertTrue(meter.ingest(run_a).accepted)
        result = meter.ingest(run_b)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "conflicting_canonical_event_id")
        report = meter.report()
        self.assertEqual(report["runs"]["run-a"]["total_tokens"], 15)
        self.assertEqual(report["runs"]["run-a"]["events_accepted"], 1)
        # Cross-run conflict must not accept on run-b.
        run_b_totals = report["runs"].get("run-b")
        if run_b_totals is not None:
            self.assertEqual(run_b_totals["events_accepted"], 0)
            self.assertEqual(run_b_totals["total_tokens"], 0)

    def test_mirror_upgrade_requires_canonical_equivalence(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        orca = base_event(
            event_id="eq-orca",
            canonical_event_id="eq-canon",
            source="orca",
            input_tokens=40,
            output_tokens=10,
            cache_tokens=0,
            cost_microusd=500,
        )
        # Higher precedence but different stage_id → conflict, not upgrade.
        provider = base_event(
            event_id="eq-provider",
            canonical_event_id="eq-canon",
            source="provider",
            stage_id="stage-other",
            input_tokens=40,
            output_tokens=10,
            cache_tokens=0,
            cost_microusd=500,
        )
        self.assertTrue(meter.ingest(orca).accepted)
        result = meter.ingest(provider)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "conflicting_canonical_event_id")
        run = meter.report()["runs"]["run-a"]
        self.assertEqual(run["total_tokens"], 50)
        self.assertEqual(run["events_accepted"], 1)

    def test_rejection_report_redacts_secret_looking_ids(self) -> None:
        marker = "sk-live-secret-marker-SHOULD-NOT-LEAK"
        meter = gate.BudgetMeter(base_budget())
        # Force a rejection path that would otherwise persist secret ids.
        result = meter.ingest(
            base_event(
                event_id=marker,
                run_id=f"run-with-{marker}",
                stage_id=f"stage-with-{marker}",
                canonical_event_id=f"canon-with-{marker}",
                input_tokens=-1,
            )
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "negative_value")
        report = meter.report()
        blob = json.dumps(report)
        self.assertNotIn(marker, blob)
        self.assertNotIn("sk-live-secret-marker", blob)
        self.assertTrue(report["rejections"])
        found_negative = False
        for rej in report["rejections"]:
            for key in ("event_id", "run_id", "stage_id", "canonical_event_id"):
                value = str(rej.get(key, ""))
                self.assertNotIn(marker, value)
            if rej["reason"] == "negative_value":
                found_negative = True
                self.assertTrue(
                    str(rej["event_id"]).startswith("redacted:"),
                    f"expected redacted event_id, got {rej['event_id']!r}",
                )
                self.assertTrue(
                    str(rej["run_id"]).startswith("redacted:"),
                    f"expected redacted run_id, got {rej['run_id']!r}",
                )
                self.assertTrue(
                    str(rej["stage_id"]).startswith("redacted:"),
                    f"expected redacted stage_id, got {rej['stage_id']!r}",
                )
                self.assertTrue(
                    str(rej["canonical_event_id"]).startswith("redacted:"),
                    f"expected redacted canonical_event_id, got {rej['canonical_event_id']!r}",
                )
        self.assertTrue(found_negative)

    def test_reportable_id_hashes_secret_markers(self) -> None:
        marker = "sk-live-secret-marker-XYZ"
        out = gate.reportable_id(marker)
        self.assertNotEqual(out, marker)
        self.assertTrue(out.startswith("redacted:"))
        self.assertNotIn(marker, out)
        # Safe ids pass through.
        self.assertEqual(gate.reportable_id("evt-001"), "evt-001")
        self.assertEqual(gate.reportable_id(""), "")

    def test_shebang_and_executable_contract(self) -> None:
        for path in (RUNNER, Path(__file__)):
            with self.subTest(path=str(path)):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
                mode = path.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{path} not user-executable")

    def test_missing_required_fields_rejected(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        for field in (
            "event_id",
            "run_id",
            "stage_id",
            "provider",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "source",
            "timestamp",
        ):
            with self.subTest(field=field):
                event = base_event(event_id=f"miss-{field}")
                del event[field]
                if field == "event_id":
                    event.pop("event_id", None)
                result = meter.ingest(event)
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "missing_field")

    def test_unknown_source_rejected(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        result = meter.ingest(base_event(event_id="bad-src", source="somewhere-else"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "unknown_source")

    def test_multiple_runs_budgeted_independently(self) -> None:
        meter = gate.BudgetMeter(base_budget(max_tokens_per_run=100))
        self.assertTrue(
            meter.ingest(
                base_event(
                    event_id="r1",
                    run_id="run-1",
                    input_tokens=90,
                    output_tokens=0,
                    cache_tokens=0,
                    cost_microusd=1,
                )
            ).accepted
        )
        self.assertTrue(
            meter.ingest(
                base_event(
                    event_id="r2",
                    run_id="run-2",
                    input_tokens=90,
                    output_tokens=0,
                    cache_tokens=0,
                    cost_microusd=1,
                )
            ).accepted
        )
        report = meter.report()
        self.assertTrue(report["passed"])
        self.assertEqual(report["runs"]["run-1"]["total_tokens"], 90)
        self.assertEqual(report["runs"]["run-2"]["total_tokens"], 90)

    def test_does_not_read_env_secrets(self) -> None:
        os.environ["OPENAI_API_KEY"] = "sk-should-never-be-read"
        os.environ["AGENT_FAMILY_SECRET"] = "top-secret"
        try:
            meter = gate.BudgetMeter(base_budget())
            meter.ingest(base_event())
            report = meter.report()
            blob = json.dumps(report)
            self.assertNotIn("sk-should-never-be-read", blob)
            self.assertNotIn("top-secret", blob)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("AGENT_FAMILY_SECRET", None)

    def test_fail_closed_validation_and_bound_reasons(self) -> None:
        """Every listed non-benign rejection fails the report (direct meter)."""
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "unknown_provider",
                base_event(event_id="fc-up", provider="not-a-provider"),
            ),
            (
                "unknown_source",
                base_event(event_id="fc-us", source="somewhere-else"),
            ),
            (
                "missing_field",
                {k: v for k, v in base_event(event_id="fc-mf").items() if k != "stage_id"},
            ),
            (
                "invalid_type",
                base_event(event_id="fc-it", provider=123),  # type: ignore[arg-type]
            ),
            (
                "non_integer_token",
                base_event(event_id="fc-nit", input_tokens="10"),  # type: ignore[arg-type]
            ),
            (
                "non_integer_money",
                base_event(event_id="fc-nim", cost_microusd=1.25),
            ),
            (
                "event_bound_exceeded",
                base_event(event_id="fc-bound", input_tokens=1, output_tokens=0, cache_tokens=0),
            ),
            (
                "negative_value",
                base_event(event_id="fc-neg", input_tokens=-1),
            ),
            (
                "overflow_value",
                base_event(event_id="fc-ovf", input_tokens=gate.MAX_TOKEN_VALUE + 1),
            ),
            (
                "forbidden_field",
                {**base_event(event_id="fc-ff"), "prompt": "nope"},
            ),
        ]

        for reason, event in cases:
            with self.subTest(reason=reason):
                if reason == "event_bound_exceeded":
                    meter = gate.BudgetMeter(base_budget(), max_events=1)
                    # Fill the bound with one accepted event.
                    self.assertTrue(
                        meter.ingest(
                            base_event(
                                event_id="fc-bound-fill",
                                input_tokens=1,
                                output_tokens=0,
                                cache_tokens=0,
                                cost_microusd=1,
                            )
                        ).accepted
                    )
                    result = meter.ingest(event)
                else:
                    meter = gate.BudgetMeter(base_budget())
                    result = meter.ingest(event)
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, reason)
                self.assertNotIn(reason, gate.BENIGN_INGEST_OUTCOMES)
                report = meter.report()
                self.assertFalse(
                    report["passed"],
                    f"{reason} must fail-close report.passed",
                )
                self.assertGreaterEqual(report["rejection_count"], 1)
                self.assertTrue(
                    any(r.get("reason") == reason for r in report["rejections"]),
                    f"{reason} must appear in rejections",
                )

    def test_cli_fail_closed_for_validation_reasons(self) -> None:
        """CLI exit nonzero for representative non-benign validation rejections."""
        cli_cases: list[tuple[str, dict[str, Any]]] = [
            ("unknown_provider", base_event(provider="not-a-provider")),
            ("unknown_source", base_event(source="nope")),
            ("event_bound_exceeded", base_event(event_id="b2")),
            ("non_integer_money", base_event(cost_microusd=0.5)),
            ("missing_field", {k: v for k, v in base_event().items() if k != "run_id"}),
            ("invalid_type", {**base_event(), "event_id": 99}),
            ("non_integer_token", base_event(output_tokens=True)),
        ]
        for reason, event in cli_cases:
            with (
                self.subTest(reason=reason),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                events_path = root / "events.jsonl"
                budget_path = root / "budget.json"
                output_path = root / "report.json"
                if reason == "event_bound_exceeded":
                    lines = [
                        json.dumps(
                            base_event(
                                event_id="b1",
                                input_tokens=1,
                                output_tokens=0,
                                cache_tokens=0,
                                cost_microusd=1,
                            )
                        ),
                        json.dumps(
                            base_event(
                                event_id="b2",
                                input_tokens=1,
                                output_tokens=0,
                                cache_tokens=0,
                                cost_microusd=1,
                            )
                        ),
                    ]
                    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    budget_path.write_text(json.dumps(base_budget()), encoding="utf-8")
                    argv = [
                        "--events",
                        str(events_path),
                        "--budget",
                        str(budget_path),
                        "--output",
                        str(output_path),
                        "--max-events",
                        "1",
                    ]
                else:
                    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
                    budget_path.write_text(json.dumps(base_budget()), encoding="utf-8")
                    argv = [
                        "--events",
                        str(events_path),
                        "--budget",
                        str(budget_path),
                        "--output",
                        str(output_path),
                    ]
                code = gate.main(argv)
                self.assertEqual(code, 1, f"{reason} CLI must exit 1")
                report = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertFalse(report["passed"], f"{reason} CLI report must fail")
                self.assertTrue(
                    any(r.get("reason") == reason for r in report["rejections"]),
                    f"{reason} missing from CLI rejections: {report['rejections']}",
                )

    def test_benign_dedupe_not_in_rejection_entries(self) -> None:
        meter = gate.BudgetMeter(base_budget())
        shared = {
            "canonical_event_id": "benign-canon",
            "input_tokens": 5,
            "output_tokens": 5,
            "cache_tokens": 0,
            "cost_microusd": 10,
        }
        self.assertTrue(
            meter.ingest(
                base_event(event_id="ben-1", source="provider", **shared)
            ).accepted
        )
        self.assertEqual(
            meter.ingest(base_event(event_id="ben-1", source="provider", **shared)).reason,
            "deduped_exact_repeat",
        )
        self.assertEqual(
            meter.ingest(base_event(event_id="ben-2", source="orca", **shared)).reason,
            "deduped_mirror",
        )
        report = meter.report()
        self.assertTrue(report["passed"])
        self.assertEqual(report["rejections"], [])
        for outcome in gate.BENIGN_INGEST_OUTCOMES:
            self.assertTrue(
                all(r.get("reason") != outcome for r in report["rejections"])
            )

    def test_future_unknown_rejection_reason_fail_closes(self) -> None:
        """Allowlist design: unknown rejection reasons fail closed by default."""
        meter = gate.BudgetMeter(base_budget())
        # Intentional private-method probe for allowlist fail-closed default.
        meter._record_rejection(
            event_id="future-1",
            run_id="run-a",
            reason="brand_new_validation_reason",
        )
        report = meter.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["rejections"][0]["reason"], "brand_new_validation_reason")

    def test_adversarial_max_events_and_unknown_provider_probes(self) -> None:
        # Adversarial flood beyond bound
        meter = gate.BudgetMeter(base_budget(), max_events=2)
        self.assertTrue(
            meter.ingest(
                base_event(event_id="adv-0", input_tokens=1, output_tokens=0, cache_tokens=0)
            ).accepted
        )
        self.assertTrue(
            meter.ingest(
                base_event(event_id="adv-1", input_tokens=1, output_tokens=0, cache_tokens=0)
            ).accepted
        )
        flood = meter.ingest(
            base_event(event_id="adv-2", input_tokens=1, output_tokens=0, cache_tokens=0)
        )
        self.assertEqual(flood.reason, "event_bound_exceeded")
        self.assertFalse(meter.report()["passed"])

        # Unknown provider alone fail-closes even with no accepted events
        meter2 = gate.BudgetMeter(base_budget())
        bad = meter2.ingest(base_event(event_id="adv-up", provider="evil-corp"))
        self.assertEqual(bad.reason, "unknown_provider")
        report2 = meter2.report()
        self.assertFalse(report2["passed"])
        self.assertEqual(report2["accepted_events"], 0)


if __name__ == "__main__":
    unittest.main()
