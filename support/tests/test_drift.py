"""Week 19: the drift benchmark. Does an approved fix go stale after the product changes, and does
retiring it help? Runs against the stateful fake of Meko and the scripted Claude."""

from __future__ import annotations

import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

from dejafix.agents.schemas import ProposedFix
from dejafix.bench import BenchTicket, DriftRow, GroundTruth, load_drift, summarize_drift
from dejafix.llm import LLMUnavailable
from dejafix.meko import MekoSession
from dejafix.tests.fake_llm import ScriptedLLM
from dejafix.tests.fakes import TEST_DATAPACK_ID
from dejafix.tests.stateful_meko import build_stateful_meko
from support import drift
from support.management.commands import drift_benchmark, seed_knowledge

PAYOUT_DOC = "Card payments settle on a T+2 business days schedule excluding weekends. See Payouts > Calendar."
UPDATE_DOC = "Product update: since December card payouts settle T+1 business day. See Payouts > Calendar."
REASON = "Outdated since December: card payments now settle T+1 business day, not T+2."
KEYS = {"ANTHROPIC_API_KEY": "test-key", "MEKO_API_KEY": "test-key", "MEKO_DATAPACK_ID": TEST_DATAPACK_ID,
        "MEKO_MCP_URL": "https://meko.test/mcp"}

TRUTH = GroundTruth(
    issues={"payout": ("T+2", "Payouts > Calendar"), "export": ("BOM",)},
    tickets=(
        BenchTicket("P1", "Where is my payout?", "Friday sales still pending on Monday.", "payout", 1, False),
        BenchTicket("E1", "Export broken", "Names look broken in Excel.", "export", 1, False),
        BenchTicket("P2", "Weekend payout", "Weekend bookings not paid out yet.", "payout", 2, False),
        BenchTicket("P3", "Saturday sales", "My Saturday sales have not arrived.", "payout", 2, False),
    ),
)
CASE = {"issue": "payout", "first": "P1", "repeats": ["P2", "P3"], "update": "data/drift/updates/payouts-2026-12.md",
        "new_keywords": ["T+1", "Payouts > Calendar"], "retire_reason": REASON}


def drift_llm(events: list[str]) -> ScriptedLLM:
    """Claude stand-in: solves with T+2 from the old docs, or with T+1 once the reason is in front of it."""
    llm = ScriptedLLM(events=events)

    def triage(prompt: str):
        if "Saturday sales" in prompt and llm.fail_saturday:  # every ticket is triaged, reused or not
            raise LLMUnavailable("simulated outage")
        return llm._default("TriageResult")

    def solve(prompt: str) -> ProposedFix:
        if "T+1" not in prompt:
            return llm._default("ProposedFix")
        return ProposedFix(
            fix_summary="Payouts now settle T+1 business day; check Payouts > Calendar.",
            fix_steps=["Open Payouts > Calendar.", "A Friday payment arrives Monday (T+1)."],
            customer_reply="Since December, payouts arrive T+1 business day after capture.",
            confidence=0.9, cited_sources=["payouts update"], needs_escalation=False,
            corrections_applied=["Uses T+1 as the human said, not T+2."],
            reasoning="The correction says payouts are now T+1.", plan=["Read the correction", "Apply T+1"],
        )

    llm.fail_saturday = False
    llm.overrides = {"TriageResult": triage, "ProposedFix": solve}
    return llm


class DriftCaseTests(SimpleTestCase):
    def test_real_cases_use_real_tickets_existing_updates_and_changed_keywords(self):
        cases = drift.cases()
        self.assertEqual([case.issue for case in cases], ["payout_weekend_delay", "webhook_duplicate_retries", "invoice_missing_tax"])
        for case in cases:
            with self.subTest(case=case.issue):
                self.assertEqual(case.first.wave, 1)
                self.assertEqual(len(case.repeats), 2)
                self.assertTrue(all(ticket.issue == case.issue for ticket in case.repeats))
                self.assertTrue(set(case.new_keywords) - set(case.old_keywords))  # the product really changed
                update = (Path(settings.BASE_DIR) / case.update).read_text(encoding="utf-8")
                for keyword in case.new_keywords:
                    self.assertIn(keyword.casefold(), update.casefold())  # the new answer is in the update

    def test_invalid_cases_are_refused(self):
        broken = {
            "unknown ticket": {**CASE, "first": "Z9"},
            "ticket of another issue": {**CASE, "repeats": ["P2", "E1"]},
            "no new keywords": {**CASE, "new_keywords": []},
            "nothing changed": {**CASE, "new_keywords": ["T+2", "Payouts > Calendar"]},
            "no reason": {**CASE, "retire_reason": " "},
            "repeat before the first": {**CASE, "first": "P2", "repeats": ["P1", "P3"]},
        }
        for label, case in broken.items():
            with self.subTest(label), self.assertRaises(ValueError):
                load_drift({"cases": [case]}, TRUTH)

    def test_summary_counts_repeats_only(self):
        def row(ticket, phase, **fields):
            base = dict(run="S1", arm="stale", issue="payout", ticket=ticket, phase=phase, source="new", correct=False,
                        stale_reuse=False, retired_skipped=False, escalated=False, human=True, tokens=100, llm_ms=10)
            return DriftRow(**{**base, **fields})

        rows = [row("P1", "first", correct=True), row("P2", "repeat", source="reused", stale_reuse=True, human=False, tokens=40),
                row("P3", "repeat", correct=True, tokens=60), row("P4", "repeat", error="LLMUnavailable", tokens=0)]
        summary = summarize_drift(rows)
        self.assertEqual((summary.repeats, summary.correct, summary.stale_reuses, summary.humans, summary.errors), (3, 1, 1, 1, 1))
        self.assertEqual(summary.avg_tokens, 50)  # failed tickets are left out of the average
        empty = summarize_drift([])
        self.assertEqual((empty.repeats, empty.correct, empty.avg_tokens), (0, 0, 0))


@override_settings(**KEYS)
class DriftRunTests(TestCase):
    def setUp(self):
        self.server, self.world = build_stateful_meko(docs=[PAYOUT_DOC])
        self.factory = lambda config, calls: MekoSession(config, observer=calls.append, server=self.server)  # noqa: E731
        self.llm = drift_llm(self.world.events)
        self.cases = load_drift({"cases": [CASE]}, TRUTH)

    def publish(self, paths):
        self.world.docs.append(UPDATE_DOC)

    def run_arm(self, arm: str, name: str = "S1"):
        return drift.run_drift(name, arm, datapack_id=TEST_DATAPACK_ID, cases=self.cases, llm=self.llm,
                               publish=self.publish, session_factory=self.factory)

    def test_stale_arm_reuses_the_outdated_fix_and_is_graded_by_the_new_keywords(self):
        first, *repeats = self.run_arm("stale")
        self.assertEqual((first.phase, first.correct, first.decision), ("first", True, "approved"))
        for row in repeats:
            with self.subTest(row.ticket):
                self.assertEqual((row.source, row.stale_reuse, row.correct, row.human), ("reused", True, False, False))
        self.assertIn(UPDATE_DOC, self.world.docs)  # the docs were updated, yet the old fix was reused
        self.assertNotIn("meko:memory_add", self.world.events[self.world.events.index("meko:memory_promote") + 1:])

    def test_retire_arm_skips_the_old_fix_learns_the_reason_and_reuses_the_new_fix(self):
        first, second, third = self.run_arm("retire", name="R1")
        self.assertTrue(first.correct)
        self.assertEqual((second.source, second.retired_skipped, second.stale_reuse, second.correct, second.decision),
                         ("new", True, False, True, "approved"))
        self.assertEqual((third.source, third.stale_reuse, third.correct, third.human), ("reused", False, True, False))
        correction = next(m for m in self.world.memories if REASON in m.text)
        self.assertEqual(correction.agent_id, "dejafix-human-review-drift-r1")

    def test_a_failed_ticket_is_recorded_and_the_run_goes_on(self):
        self.llm.fail_saturday = True
        rows = self.run_arm("retire", name="R2")
        self.assertEqual([row.ticket for row in rows], ["P1", "P2", "P3"])
        self.assertEqual(rows[2].error.split(":")[0], "LLMUnavailable")
        self.assertFalse(rows[2].correct)


@override_settings(**KEYS)
class DriftCommandTests(TestCase):
    def setUp(self):
        self.server, self.world = build_stateful_meko(docs=[PAYOUT_DOC])
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        factory = lambda config, calls: MekoSession(config, observer=calls.append, server=self.server)  # noqa: E731
        llm = drift_llm(self.world.events)
        patches = [
            mock.patch.object(drift_benchmark.Command, "session_factory", staticmethod(factory)),
            mock.patch.object(drift_benchmark.Command, "llm_factory", staticmethod(lambda: llm)),
            mock.patch.object(drift_benchmark.Command, "publisher", staticmethod(lambda datapack_id, paths: self.world.docs.append(UPDATE_DOC))),
            mock.patch.object(drift_benchmark.Command, "results_dir", self.folder),
            mock.patch.object(drift, "cases", lambda: load_drift({"cases": [CASE]}, TRUTH)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_runs_write_the_results_and_the_report_and_names_are_not_reused(self):
        call_command("drift_benchmark", "run", "S1", "--arm", "stale", "--skip-seed", stdout=StringIO())
        call_command("drift_benchmark", "run", "R1", "--arm", "retire", "--skip-seed", stdout=StringIO())
        results = json.loads((self.folder / "drift.json").read_text(encoding="utf-8"))
        self.assertEqual({name: run["arm"] for name, run in results["runs"].items()}, {"S1": "stale", "R1": "retire"})
        report = (self.folder / "drift.md").read_text(encoding="utf-8")
        self.assertIn("| Correct after the change, of 2 | 0 | 2 |", report)
        self.assertIn("| Reused the outdated fix, of 2 | 2 | 0 |", report)
        with self.assertRaisesMessage(CommandError, "already"):
            call_command("drift_benchmark", "run", "S1", "--arm", "stale", "--skip-seed", stdout=StringIO())

    def test_a_run_with_failed_tickets_is_left_out_of_the_comparison_and_named(self):
        # Live, week 19: run S2 hit Meko's free-tier quota halfway; comparing it would be unfair.
        call_command("drift_benchmark", "run", "S1", "--arm", "stale", "--skip-seed", stdout=StringIO())

        def quota_reached_after_the_first_tickets(datapack_id, paths):
            self.world.fail_tool, self.world.fail_code = "conversation_add_message", "free_tier_limit_reached"

        with mock.patch.object(drift_benchmark.Command, "publisher", staticmethod(quota_reached_after_the_first_tickets)):
            call_command("drift_benchmark", "run", "S2", "--arm", "stale", "--skip-seed", stdout=StringIO())
        report = (self.folder / "drift.md").read_text(encoding="utf-8")
        table = report.split("## Every repeat")[0]
        self.assertIn("S1 · stale", table)
        self.assertNotIn("S2", table)
        self.assertIn("## Runs left out", report)
        self.assertIn("S2 (stale): 2 of 3 tickets failed", report)
        self.assertIn("free_tier_limit_reached", report)

    def test_report_is_stored_in_meko_as_an_artifact(self):
        call_command("drift_benchmark", "run", "S1", "--arm", "stale", "--skip-seed", stdout=StringIO())
        call_command("drift_benchmark", "report", stdout=StringIO())
        names = [artifact["filename"] for artifact in self.world.artifacts.values()]
        self.assertIn("dejafix-drift-report.json", names)

    def test_bad_names_and_missing_keys_are_refused(self):
        with self.assertRaisesMessage(CommandError, "letters"):
            call_command("drift_benchmark", "run", "../x", "--arm", "stale", "--skip-seed", stdout=StringIO())
        with override_settings(ANTHROPIC_API_KEY=""), self.assertRaisesMessage(CommandError, "CLAUDE_API_KEY"):
            call_command("drift_benchmark", "run", "S2", "--arm", "stale", "--skip-seed", stdout=StringIO())
        self.assertFalse((self.folder / "drift.json").exists())


class SeedSingleFilesTests(SimpleTestCase):
    def test_file_option_uploads_only_the_named_files(self):
        command = seed_knowledge.Command()
        update = Path(settings.BASE_DIR) / "data" / "drift" / "updates" / "payouts-2026-12.md"
        self.assertEqual(command.files_to_upload(folder=None, files=[update]), [update])
        with self.assertRaises(CommandError):
            command.files_to_upload(folder=None, files=[update.with_suffix(".exe")])
