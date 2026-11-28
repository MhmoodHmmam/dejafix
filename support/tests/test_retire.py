"""Retiring an approved fix that turned out wrong or outdated (week 17).

Meko can't delete or update a promoted memory through MCP yet (live probe, decision log R16), so
DejaFix records the retirement, keeps the fix out of every later retrieval, and stores the reason
in Meko as a human correction the next resolver reads.
"""

from __future__ import annotations

import json

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from dejafix.agents.memory import CORRECTION_HEADER
from support.models import MAX_NOTE_LENGTH, AgentRun, Resolution, Ticket
from support.services import ReviewError, approve_resolution, retire_fix
from support.tests.test_views import ViewTestCase

REASON = "Payouts moved to T+1 in May; tell customers to expect the money the next business day."


class RetireFixTests(ViewTestCase):
    def shared_fix(self) -> Resolution:
        """T1 solved and approved (now collective memory), T2 closed by reusing it."""
        source = self.resolved_ticket("T1")
        resolution = approve_resolution(source.resolution, user=self.staff, session_factory=self.factory)
        repeat = self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")
        self.assertEqual(repeat.resolution.reused_memory_id, resolution.promoted_memory_id)
        return resolution

    def url(self, memory_id: str) -> str:
        return reverse("support:memory_retire", args=[memory_id])

    def retire(self, resolution: Resolution, note: str = REASON):
        return self.client.post(self.url(resolution.promoted_memory_id), {"note": note})

    # --- acceptance criteria -----------------------------------------------------------------

    def test_retired_fix_is_never_offered_again(self):
        resolution = self.shared_fix()
        response = self.retire(resolution)
        self.assertRedirects(response, reverse("support:memory"))

        ticket = self.resolved_ticket("T3", body="My Saturday sales have not arrived.")
        self.assertEqual(ticket.resolution.source, Resolution.Source.NEW)
        self.assertEqual(ticket.resolution.reused_memory_id, "")
        retrieve = AgentRun.objects.get(ticket=ticket, step="retrieve").decision
        self.assertIsNone(retrieve["reuse_candidate"])
        self.assertEqual(retrieve["retired_fixes_skipped"], [resolution.promoted_memory_id])

    def test_the_reason_becomes_a_learning_the_next_resolver_reads(self):
        self.retire(self.shared_fix())
        learning = next(m for m in self.world.memories if m.text.startswith(CORRECTION_HEADER))
        self.assertEqual(learning.agent_id, "dejafix-human-review")
        self.assertIn(REASON, learning.text)

        self.llm.calls.clear()
        self.resolved_ticket("T3", body="My Saturday sales have not arrived.")
        solve_prompt = next(call.prompt for call in self.llm.calls if call.schema == "ProposedFix")
        self.assertIn("Payouts moved to T+1 in May", solve_prompt)

    def test_retirement_is_recorded_with_a_case_file_and_a_trace(self):
        resolution = self.shared_fix()
        self.retire(resolution)
        resolution.refresh_from_db()
        self.assertEqual((resolution.retired_by, resolution.retirement_note), (self.staff, REASON))
        self.assertIsNotNone(resolution.retired_at)

        artifact = self.world.artifacts[resolution.retirement_case_file_hash]
        self.assertEqual(artifact["filename"], "dejafix-case-T1-retired.json")
        case_file = json.loads(artifact["content"])
        self.assertEqual((case_file["human_decision"], case_file["human_note"]), ("retired", REASON))
        self.assertEqual(case_file["retired_memory_id"], resolution.promoted_memory_id)
        self.assertEqual(case_file["reused_by"], ["T2"])

        turns = [json.loads(m["output"]) for m in self.world.messages.values() if '"retired_memory_id"' in m["output"]]
        self.assertEqual([turn["retired_memory_id"] for turn in turns], [resolution.promoted_memory_id])

    def test_tickets_that_reused_the_retired_fix_are_flagged(self):
        self.retire(self.shared_fix())
        page = self.client.get(reverse("support:ticket_detail", args=["T2"]))
        self.assertContains(page, "Reused a retired fix")
        self.assertContains(page, "Payouts moved to T+1 in May")
        self.assertNotContains(self.client.get(reverse("support:ticket_detail", args=["T1"])), "Reused a retired fix")

    def test_memory_page_lists_retired_fixes_apart_in_the_same_queries(self):
        resolution = self.shared_fix()
        self.retire(resolution)
        page = self.client.get(reverse("support:memory"))
        overview = page.context["memory"]
        self.assertEqual(overview.fixes, [])
        self.assertEqual([fix.memory_id for fix in overview.retired], [resolution.promoted_memory_id])
        self.assertContains(page, "Retired fixes")
        self.assertContains(page, REASON)
        self.assertEqual(overview.tokens_saved, 0)  # a retired fix no longer counts as shared memory
        self.assertEqual(overview.learning_count, 1)  # its reason is a human correction in Meko
        # session, user, approved fixes, their reuses, token sums, corrections
        with self.assertNumQueries(6):
            self.client.get(reverse("support:memory"))

    # --- invalid input, access and routing ---------------------------------------------------

    def test_a_reason_is_required_and_nothing_changes_without_one(self):
        resolution = self.shared_fix()
        events = len(self.world.events)
        for note in ("", "   ", "x" * (MAX_NOTE_LENGTH + 1)):
            with self.subTest(note=note[:5]):
                response = self.retire(resolution, note=note)
                self.assertEqual(response.status_code, 400)
        resolution.refresh_from_db()
        self.assertIsNone(resolution.retired_at)
        self.assertEqual(len(self.world.events), events)  # Meko was never called

    def test_access_and_routing(self):
        resolution = self.shared_fix()
        url = self.url(resolution.promoted_memory_id)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(self.url("not-a-shared-fix"), {"note": REASON}).status_code, 404)
        not_shared = self.resolved_ticket("T9", body="Something else entirely about invoices.").resolution
        self.assertEqual(not_shared.promoted_memory_id, "")  # no memory of its own
        self.assertEqual(self.client.post(self.url(str(not_shared.pk)), {"note": REASON}).status_code, 404)
        self.client.force_login(self.viewer)  # staff, but may not change resolutions
        self.assertEqual(self.client.post(url, {"note": REASON}).status_code, 403)
        self.client.logout()
        response = self.client.post(url, {"note": REASON})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        resolution.refresh_from_db()
        self.assertIsNone(resolution.retired_at)

    # --- state conflicts and failures --------------------------------------------------------

    def test_retiring_twice_is_a_conflict(self):
        resolution = self.shared_fix()
        self.retire(resolution)
        events = len(self.world.events)
        response = self.retire(resolution, note="Another reason.")
        self.assertEqual(response.status_code, 409)
        resolution.refresh_from_db()
        self.assertEqual(resolution.retirement_note, REASON)
        self.assertEqual(len(self.world.events), events)
        with self.assertRaises(ReviewError):
            retire_fix(resolution, user=self.staff, note="Again.", session_factory=self.factory)

    def test_meko_failure_leaves_the_fix_shared(self):
        resolution = self.shared_fix()
        self.world.fail_tool = "memory_add"
        response = self.retire(resolution)
        self.assertEqual(response.status_code, 502)
        self.assertContains(response, "nothing was retired", status_code=502)
        resolution.refresh_from_db()
        self.assertEqual((resolution.retired_at, resolution.retired_by, resolution.retirement_note), (None, None, ""))

        self.world.fail_tool = None
        ticket = self.resolved_ticket("T3", body="My Saturday sales have not arrived.")
        self.assertEqual(ticket.resolution.reused_memory_id, resolution.promoted_memory_id)

    @override_settings(MEKO_API_KEY="")
    def test_without_keys_nothing_is_retired(self):
        resolution = self.shared_fix()
        response = self.retire(resolution)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "MEKO_API_KEY", status_code=503)
        resolution.refresh_from_db()
        self.assertIsNone(resolution.retired_at)

    def test_the_database_only_retires_a_shared_fix(self):
        pending = self.resolved_ticket("T9", body="Something else entirely about invoices.").resolution
        with self.assertRaises(IntegrityError), transaction.atomic():
            Resolution.objects.filter(pk=pending.pk).update(retired_at=timezone.now())
        self.assertEqual(Ticket.objects.get(pk=pending.ticket_id).status, pending.ticket.status)
