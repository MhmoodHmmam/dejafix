"""Week 12 security review: each test reproduces one finding before its fix."""

from __future__ import annotations

from unittest import mock

import httpx2
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from dejafix.agents.knowledge import post_to_presigned_url
from dejafix.llm import LLMUnavailable
from dejafix.meko import MekoUnavailable
from dejafix.tests.fake_llm import ScriptedLLM
from dejafix.tests.stateful_meko import build_stateful_meko
from support import services
from support.models import KnowledgeGap, Resolution, Ticket
from support.services import approve_resolution, reject_resolution, resolve_ticket
from support.tests.test_gaps import GapTestCase
from support.tests.test_views import ViewTestCase

PAYOUT_DOC = "Card payments settle on a T+2 business days schedule excluding weekends. See Payouts > Calendar."


@override_settings(DEJAFIX_CURATOR_AGENT="dejafix-curator")
class ServiceHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reviewer = get_user_model().objects.create_user("reviewer", password="unused-in-tests", is_staff=True)

    def setUp(self):
        self.server, self.world = build_stateful_meko(docs=[PAYOUT_DOC])
        self.factory = self.world.session_factory(self.server)
        self.llm = ScriptedLLM(events=self.world.events)

    def resolved(self, ref: str = "T1") -> Ticket:
        ticket = Ticket.objects.create(external_id=ref, subject="Where is my payout?", body="Friday sales pending.", customer="Shop")
        return resolve_ticket(ticket, llm=self.llm, session_factory=self.factory).ticket

    def test_a_failed_run_does_not_reopen_a_ticket_another_run_closed(self):
        # Finding 1: two concurrent runs; the other one finished and the fix was approved meanwhile.
        ticket = Ticket.objects.create(external_id="T1", subject="s", body="b", customer="c")
        self.llm.fail_with = LLMUnavailable("down")
        real_async_to_sync = services.async_to_sync

        def finished_elsewhere_while_this_run_failed(function):
            run = real_async_to_sync(function)

            def wrapper(*args, **kwargs):
                try:
                    return run(*args, **kwargs)
                except LLMUnavailable:
                    Ticket.objects.filter(pk=ticket.pk).update(status=Ticket.Status.CLOSED)
                    raise

            return wrapper

        with mock.patch.object(services, "async_to_sync", finished_elsewhere_while_this_run_failed), \
             self.assertLogs("dejafix", "WARNING"):
            result = resolve_ticket(ticket, llm=self.llm, session_factory=self.factory)
        self.assertTrue(result.error)
        self.assertEqual(Ticket.objects.get(pk=ticket.pk).status, Ticket.Status.CLOSED)

    def test_approval_stands_when_only_the_closing_trace_write_fails(self):
        # Finding 3: the fix was already promoted; rolling the approval back would hide shared memory.
        ticket = self.resolved()
        self.world.fail_tool = "conversation_add_message"
        with self.assertLogs("dejafix", "WARNING"):
            resolution = approve_resolution(ticket.resolution, user=self.reviewer, session_factory=self.factory)
        self.assertEqual(resolution.review_status, Resolution.Review.APPROVED)
        self.assertTrue(resolution.promoted_memory_id)
        self.assertTrue(self.world.memory_by_id(resolution.promoted_memory_id).promoted)

    def test_rejection_stands_when_only_the_closing_trace_write_fails(self):
        ticket = self.resolved()
        self.world.fail_tool = "conversation_add_message"
        with self.assertLogs("dejafix", "WARNING"):
            resolution = reject_resolution(ticket.resolution, user=self.reviewer, note="Mention the calendar.", session_factory=self.factory)
        self.assertEqual(resolution.review_status, Resolution.Review.REJECTED)

    def test_a_null_promote_reply_is_not_a_crash(self):
        # Finding 2b: {"inserted_ids": null} used to raise TypeError and leave a half-approved fix.
        ticket = self.resolved()
        self.world.promote_returns_null_ids = True
        resolution = approve_resolution(ticket.resolution, user=self.reviewer, session_factory=self.factory)
        self.assertEqual(resolution.review_status, Resolution.Review.APPROVED)
        self.assertTrue(resolution.promoted_memory_id)


class EscalatedApprovalTests(ViewTestCase):
    def escalated_ticket(self) -> Ticket:
        self.llm.solve_confidence = 0.2  # the code rule escalates a low-confidence fix
        ticket = self.resolved_ticket()
        self.assertEqual(ticket.status, Ticket.Status.ESCALATED)
        return ticket

    def test_approving_an_escalated_fix_needs_a_written_reason(self):
        # Review note: one click could put an escalated (e.g. injection) fix into shared memory.
        ticket = self.escalated_ticket()
        url = reverse("support:resolution_approve", args=[ticket.external_id])
        page = self.client.get(reverse("support:ticket_detail", args=[ticket.external_id]))
        self.assertContains(page, "The reviewer agent escalated this fix")
        response = self.client.post(url, {"note": "  "})
        self.assertContains(response, "write why you approve it", status_code=400)
        self.assertEqual(Resolution.objects.get(ticket=ticket).review_status, "pending")
        self.assertNotIn("meko:memory_promote", self.world.events)

        self.client.post(url, {"note": "Checked against the payouts doc; the fix is right."})
        self.assertEqual(Resolution.objects.get(ticket=ticket).review_status, "approved")

    def test_service_enforces_the_reason_too(self):
        ticket = self.escalated_ticket()
        from support.services import ReviewError
        with self.assertRaises(ReviewError):
            approve_resolution(ticket.resolution, user=self.staff, note="", session_factory=self.factory)


class PublishHardeningTests(GapTestCase):
    def test_publishing_sends_the_draft_saved_after_the_page_was_loaded(self):
        # Finding 6: the article text must be read after the claim, not before.
        gap = self.drafted_gap()
        KnowledgeGap.objects.filter(pk=gap.pk).update(draft_body="The newer body, saved a moment ago.")
        self.publish(gap)  # `gap` still holds the older body
        self.assertIn("The newer body, saved a moment ago.", self.world.docs[-1])


class UploadHardeningTests(SimpleTestCase):
    def test_a_network_error_during_upload_is_a_meko_error(self):
        # Finding 2: httpx2 errors escaped untranslated and left the gap stuck in PUBLISHING.
        class FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                raise httpx2.ConnectError("connection refused")

        with mock.patch("dejafix.agents.knowledge.httpx2.AsyncClient", FailingClient), self.assertRaises(MekoUnavailable):
            async_to_sync(post_to_presigned_url)("https://storage.example.test/upload", {}, "a.md", b"x", "text/markdown")
