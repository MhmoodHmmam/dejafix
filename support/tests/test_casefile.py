"""Case files: the decision record stored in Meko's artifact store when a human decides."""

from __future__ import annotations

import hashlib
import json

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from dejafix.agents.memory import format_correction_memory, format_fix_memory, is_correction_memory, parse_fix_memory
from dejafix.meko import MekoToolError
from dejafix.tests.fake_llm import ScriptedLLM
from dejafix.tests.stateful_meko import build_stateful_meko
from support.models import Resolution, Ticket
from support.services import approve_resolution, reject_resolution, resolve_ticket

PAYOUT_DOC = "Card payments settle on a T+2 business days schedule excluding weekends. See Payouts > Calendar."
CURATOR = "dejafix-curator"
TICKET_BODY = "Our Friday sales from the Ayla Bakery shop are still pending on Monday."


@override_settings(DEJAFIX_CURATOR_AGENT=CURATOR)
class CaseFileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reviewer = get_user_model().objects.create_user("dana.k", password="unused-in-tests", is_staff=True)

    def setUp(self):
        self.server, self.world = build_stateful_meko(docs=[PAYOUT_DOC])
        self.factory = self.world.session_factory(self.server)
        self.llm = ScriptedLLM(events=self.world.events)
        self.ticket = Ticket.objects.create(external_id="T1", subject="Where is my payout?", body=TICKET_BODY, customer="Ayla Bakery")
        resolve_ticket(self.ticket, llm=self.llm, session_factory=self.factory)

    def approve(self) -> Resolution:
        resolution = Resolution.objects.select_related("ticket").get(ticket=self.ticket)
        return approve_resolution(resolution, user=self.reviewer, session_factory=self.factory)

    def only_artifact(self) -> tuple[str, dict]:
        self.assertEqual(len(self.world.artifacts), 1)
        return next(iter(self.world.artifacts.items()))

    def test_approval_stores_the_case_file_and_links_it_from_shared_memory(self):
        resolution = self.approve()
        content_hash, artifact = self.only_artifact()
        self.assertEqual(resolution.case_file_hash, content_hash)
        self.assertEqual(hashlib.sha256(artifact["content"]).hexdigest(), content_hash)
        self.assertEqual((artifact["filename"], artifact["agent_id"]), ("dejafix-case-T1.json", CURATOR))
        memory = self.world.memory_by_id(resolution.promoted_memory_id)
        self.assertEqual(parse_fix_memory(memory.text).case_file, content_hash)
        events = self.world.events
        self.assertLess(events.index("meko:artifact_put"), events.index("meko:memory_add"))

    def test_case_file_records_the_decision_but_not_the_ticket_customer_or_reviewer(self):
        self.approve()
        _, artifact = self.only_artifact()
        document = json.loads(artifact["content"])
        self.assertEqual((document["kind"], document["human_decision"], document["ticket"]), ("case_file", "approved", "T1"))
        self.assertEqual([step["step"] for step in document["agent_steps"]], ["triage", "retrieve", "solve", "review"])
        self.assertEqual(document["fix"]["fix_summary"], Resolution.objects.get(ticket=self.ticket).fix_summary)
        raw = artifact["content"].decode("utf-8")
        for private in ("Ayla", "Friday sales", "dana.k"):
            self.assertNotIn(private, raw)

    def test_rejection_stores_a_case_file_with_the_reviewer_note(self):
        resolution = Resolution.objects.select_related("ticket").get(ticket=self.ticket)
        resolution = reject_resolution(resolution, user=self.reviewer, note="Check the bank holiday calendar.", session_factory=self.factory)
        content_hash, artifact = self.only_artifact()
        document = json.loads(artifact["content"])
        self.assertEqual((document["human_decision"], document["human_note"]), ("rejected", "Check the bank holiday calendar."))
        self.assertEqual(resolution.case_file_hash, content_hash)
        learning = self.world.memories[0].text
        self.assertTrue(is_correction_memory(learning))
        self.assertIn(f"Case file: {content_hash}", learning)

    def test_case_file_failure_releases_the_claim_and_writes_no_memory(self):
        self.world.fail_tool = "artifact_put"
        with self.assertRaises(MekoToolError), self.assertLogs("dejafix", "WARNING"):
            self.approve()
        resolution = Resolution.objects.get(ticket=self.ticket)
        self.assertEqual((resolution.review_status, resolution.case_file_hash), ("pending", ""))
        self.assertEqual(self.world.memories, [])

    def test_retried_approval_stores_the_same_case_file_once(self):
        self.world.fail_tool = "memory_promote"
        with self.assertRaises(MekoToolError), self.assertLogs("dejafix", "WARNING"):
            self.approve()
        self.world.fail_tool = None
        self.approve()
        self.assertEqual(len(self.world.artifacts), 1)
        self.assertEqual(self.world.events.count("meko:artifact_put"), 2)


class MemoryFormatCompatibilityTests(SimpleTestCase):
    def test_fix_memory_without_a_case_file_still_parses(self):
        # The fix promoted in the week 2 live run has no case-file line.
        text = format_fix_memory(signature="Payout pending", category="payouts", summary="Explain T+2.", steps=["Open Payouts."], ticket_ref="T001")
        parsed = parse_fix_memory(text)
        self.assertEqual((parsed.summary, parsed.case_file), ("Explain T+2.", ""))
        with_file = format_fix_memory(
            signature="Payout pending", category="payouts", summary="Explain T+2.", steps=["Open Payouts."], ticket_ref="T001", case_file="ab" * 32
        )
        self.assertEqual(parse_fix_memory(with_file).steps, ("Open Payouts.",))
        self.assertTrue(is_correction_memory(format_correction_memory(signature="s", rejected_summary="r", note="n", case_file="ab" * 32)))
