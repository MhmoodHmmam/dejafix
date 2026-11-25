"""Staff pages: access, methods, CSRF, review actions, gaps, benchmark, dashboard and headers."""

from __future__ import annotations

import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from dejafix.llm import LLMRefused
from dejafix.meko import MekoSession
from dejafix.tests.fake_llm import ScriptedLLM
from dejafix.tests.fakes import TEST_DATAPACK_ID
from dejafix.tests.stateful_meko import WeakSearch, build_stateful_meko
from support.middleware import CONTENT_SECURITY_POLICY
from support.models import AgentRun, BenchmarkResult, BenchmarkRun, KnowledgeGap, Resolution, Ticket
from support.services import resolve_ticket

PAYOUT_DOC = "Card payments settle on a T+2 business days schedule excluding weekends. See Payouts > Calendar."
MEKO_SETTINGS = {"MEKO_API_KEY": "test-key", "MEKO_DATAPACK_ID": TEST_DATAPACK_ID, "MEKO_MCP_URL": "https://meko.test/mcp"}


@override_settings(DEJAFIX_CURATOR_AGENT="dejafix-curator", **MEKO_SETTINGS)
class ViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.staff = users.create_superuser("lead", password="unused-in-tests")
        cls.viewer = users.create_user("viewer", password="unused-in-tests", is_staff=True)  # staff, no change perms
        cls.plain = users.create_user("plain", password="unused-in-tests")

    def setUp(self):
        cache.clear()
        self.server, self.world = build_stateful_meko(docs=[PAYOUT_DOC])
        self.llm = ScriptedLLM(events=self.world.events)
        factory = self.world.session_factory(self.server)
        quality_factory = lambda config: MekoSession(config, server=self.server)  # noqa: E731
        patches = [
            mock.patch("support.services.default_session_factory", factory),
            mock.patch("support.gaps.default_uploader", lambda: self.world.receive_upload),
            mock.patch("support.views.llm_factory", lambda: self.llm),
            mock.patch("support.dashboard.default_quality_session", quality_factory),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.factory = factory
        self.client.force_login(self.staff)

    def resolved_ticket(self, ref: str = "T1", body: str = "Friday sales still pending on Monday.") -> Ticket:
        ticket = Ticket.objects.create(external_id=ref, subject="Where is my payout?", body=body, customer="Shop")
        return resolve_ticket(ticket, llm=self.llm, session_factory=self.factory).ticket

    def make_gap(self, status: str = KnowledgeGap.Status.DRAFTED, **fields) -> KnowledgeGap:
        values = dict(query="Payout delayed after a bank holiday", log_id="log-1", agent="dejafix-resolver-a",
                      band_before="mid", score_before=0.68, draft_title="Holiday payouts", draft_body="Payouts settle T+2.")
        values.update(fields)
        return KnowledgeGap.objects.create(status=status, **values)


class AccessTests(ViewTestCase):
    def page_urls(self) -> list[str]:
        ticket = self.resolved_ticket()
        gap = self.make_gap()
        return [
            reverse("support:dashboard"), reverse("support:ticket_list"), reverse("support:ticket_detail", args=[ticket.external_id]),
            reverse("support:gap_list"), reverse("support:gap_detail", args=[gap.pk]), reverse("support:benchmark"),
        ]

    def test_every_page_is_staff_only(self):
        urls = self.page_urls()
        anonymous = Client()
        for url in urls:
            with self.subTest(url=url):
                response = anonymous.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/login/?next="))
        plain = Client()
        plain.force_login(self.plain)
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(plain.get(url).status_code, 403)
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def action_urls(self) -> list[str]:
        ticket = self.resolved_ticket()
        gap = self.make_gap()
        ref = ticket.external_id
        return [
            reverse("support:ticket_resolve", args=[ref]), reverse("support:resolution_approve", args=[ref]),
            reverse("support:resolution_reject", args=[ref]), reverse("support:gap_save", args=[gap.pk]),
            reverse("support:gap_publish", args=[gap.pk]), reverse("support:gap_dismiss", args=[gap.pk]),
        ]

    def test_actions_only_accept_post(self):
        for url in self.action_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_actions_need_the_change_permission(self):
        urls = self.action_urls()
        events_before = len(self.world.events)
        viewer = Client()
        viewer.force_login(self.viewer)
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(viewer.post(url, {"note": "x"}).status_code, 403)
        self.assertEqual(len(self.world.events), events_before)
        self.assertEqual(Resolution.objects.get().review_status, "pending")

    def test_post_without_a_csrf_token_is_refused(self):
        ticket = self.resolved_ticket()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.staff)
        response = strict.post(reverse("support:resolution_approve", args=[ticket.external_id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Resolution.objects.get().review_status, "pending")

    def test_unknown_ticket_or_gap_is_404(self):
        self.assertEqual(self.client.get(reverse("support:ticket_detail", args=["NOPE"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("support:gap_detail", args=[999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("support:resolution_approve", args=["NOPE"])).status_code, 404)

    def test_pages_send_a_strict_csp_but_the_admin_keeps_its_own(self):
        self.assertEqual(self.client.get(reverse("support:dashboard"))["Content-Security-Policy"], CONTENT_SECURITY_POLICY)
        self.assertIn("script-src 'none'", CONTENT_SECURITY_POLICY)
        self.assertIn("Content-Security-Policy", Client().get(reverse("login")).headers)
        self.assertNotIn("Content-Security-Policy", self.client.get("/admin/").headers)

    def test_logout_is_post_only(self):
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)


class TicketReviewTests(ViewTestCase):
    def approve(self, ref: str = "T1", **data):
        return self.client.post(reverse("support:resolution_approve", args=[ref]), data)

    def test_approving_on_the_page_shares_the_fix_and_redirects(self):
        self.resolved_ticket()
        response = self.client.post(reverse("support:resolution_approve", args=["T1"]), {"note": "Matches the payout docs."}, follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("support:ticket_detail", args=["T1"]), 302)])
        self.assertContains(response, "approved and shared as collective memory")
        resolution = Resolution.objects.get()
        self.assertEqual((resolution.review_status, resolution.reviewed_by, resolution.human_note), ("approved", self.staff, "Matches the payout docs."))
        self.assertEqual(self.world.memory_by_id(resolution.promoted_memory_id).agent_id, "dejafix-curator")
        self.assertContains(response, resolution.case_file_hash)

    def test_approving_twice_is_a_conflict(self):
        self.resolved_ticket()
        self.approve()
        response = self.approve()
        self.assertContains(response, "already reviewed", status_code=409)
        self.assertEqual(self.world.events.count("meko:memory_promote"), 1)

    def test_meko_failure_on_approval_is_502_and_changes_nothing(self):
        self.resolved_ticket()
        self.world.fail_tool = "memory_promote"
        with self.assertLogs("dejafix", "WARNING"):
            response = self.approve()
        self.assertContains(response, "Meko failed, nothing was shared", status_code=502)
        self.assertEqual(Resolution.objects.get().review_status, "pending")

    def test_rejection_needs_a_note_of_sensible_length(self):
        self.resolved_ticket()
        url = reverse("support:resolution_reject", args=["T1"])
        self.assertContains(self.client.post(url, {"note": "  "}), "A rejection needs a note", status_code=400)
        self.assertEqual(self.client.post(url, {"note": "x" * 2001}).status_code, 400)
        self.assertEqual(self.world.memories, [])
        response = self.client.post(url, {"note": "Check the bank holiday calendar first."})
        self.assertRedirects(response, reverse("support:ticket_detail", args=["T1"]))
        self.assertEqual(Resolution.objects.get().review_status, "rejected")
        self.assertEqual(self.world.memories[0].agent_id, "dejafix-human-review")

    def test_running_the_agents_from_the_page(self):
        Ticket.objects.create(external_id="T9", subject="Where is my payout?", body="Pending since Friday.", customer="Shop")
        url = reverse("support:ticket_resolve", args=["T9"])
        self.assertRedirects(self.client.post(url), reverse("support:ticket_detail", args=["T9"]))
        self.assertEqual(Ticket.objects.get(external_id="T9").status, Ticket.Status.AWAITING_REVIEW)
        self.assertContains(self.client.post(url), "already resolved", status_code=409)

    def test_claude_failure_when_running_the_agents_is_502(self):
        Ticket.objects.create(external_id="T9", subject="s", body="b", customer="c")
        self.llm.fail_with = LLMRefused("declined")
        with self.assertLogs("dejafix.support", "WARNING"):
            response = self.client.post(reverse("support:ticket_resolve", args=["T9"]))
        self.assertContains(response, "The agents failed", status_code=502)
        self.assertEqual(Ticket.objects.get(external_id="T9").status, Ticket.Status.FAILED)

    def test_ticket_page_explains_each_step_and_escapes_customer_text(self):
        self.resolved_ticket(body='<script>alert("x")</script> لم تصل الدفعة')
        page = self.client.get(reverse("support:ticket_detail", args=["T1"]))
        self.assertNotContains(page, '<script>alert("x")</script>', html=False)
        self.assertContains(page, "&lt;script&gt;")
        self.assertContains(page, 'class="ticket-body" dir="auto"')
        for step in ("Triage", "Retrieve", "Solve", "Review"):
            self.assertContains(page, f"<strong>{step}</strong>")
        self.assertContains(page, "The payouts doc defines T+2 business days.")
        self.assertContains(page, "Approve and share with every agent")

    def test_reused_ticket_page_shows_the_shared_memory_it_used(self):
        self.resolved_ticket()
        self.approve()
        repeat = self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")
        page = self.client.get(reverse("support:ticket_detail", args=[repeat.external_id]))
        self.assertContains(page, "Reused an approved fix")
        self.assertContains(page, repeat.resolution.reused_memory_id)
        self.assertContains(page, "<strong>Reuse check</strong>")
        self.assertNotContains(page, "Approve and share with every agent")


class ListTests(ViewTestCase):
    def test_status_filter_is_whitelisted_and_pages_are_checked(self):
        self.resolved_ticket()
        url = reverse("support:ticket_list")
        self.assertContains(self.client.get(url, {"status": "awaiting_review"}), "T1")
        self.assertNotContains(self.client.get(url, {"status": "closed"}), ">T1<")
        self.assertEqual(self.client.get(url, {"status": "drop table"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"page": "abc"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"page": "99"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("support:gap_list"), {"status": "nope"}).status_code, 400)

    def test_ticket_list_pages_by_50_with_a_constant_query_count(self):
        Ticket.objects.bulk_create(Ticket(external_id=f"T{n:03}", subject="s", body="b", customer="c") for n in range(60))
        url = reverse("support:ticket_list")
        # session, user, count, page with the joined resolution
        with self.assertNumQueries(4):
            first = self.client.get(url)
        self.assertEqual(len(first.context["page"].object_list), 50)
        self.assertContains(self.client.get(url, {"page": 2}), "T059")


class GapPageTests(ViewTestCase):
    def test_draft_can_be_edited_then_published_then_is_read_only(self):
        gap = self.make_gap()
        save = reverse("support:gap_save", args=[gap.pk])
        self.assertRedirects(self.client.post(save, {"draft_title": "Payouts after holidays", "draft_body": "T+2 business days."}), reverse("support:gap_detail", args=[gap.pk]))
        gap.refresh_from_db()
        self.assertEqual(gap.draft_title, "Payouts after holidays")
        self.assertEqual(self.client.post(save, {"draft_title": "x" * 121, "draft_body": "b"}).status_code, 400)
        self.assertRedirects(self.client.post(reverse("support:gap_publish", args=[gap.pk])), reverse("support:gap_detail", args=[gap.pk]))
        gap.refresh_from_db()
        self.assertEqual((gap.status, gap.published_by), ("published", self.staff))
        self.assertIn("# Payouts after holidays", self.world.docs[-1])
        self.assertContains(self.client.post(reverse("support:gap_publish", args=[gap.pk])), "Only drafted gaps", status_code=409)
        self.assertContains(self.client.post(save, {"draft_title": "t", "draft_body": "b"}), "be edited", status_code=409)
        self.assertContains(self.client.post(reverse("support:gap_dismiss", args=[gap.pk])), "be dismissed", status_code=409)
        self.assertNotContains(self.client.get(reverse("support:gap_detail", args=[gap.pk])), 'name="draft_body"')

    def test_empty_draft_cannot_be_published(self):
        gap = self.make_gap(KnowledgeGap.Status.NEEDS_AUTHOR, draft_title="", draft_body="")
        response = self.client.post(reverse("support:gap_publish", args=[gap.pk]))
        self.assertContains(response, "Write a title and an article body", status_code=409)
        self.assertNotIn("meko:knowledgebase_upload_document", self.world.events)

    def test_dismissing_an_open_gap(self):
        gap = self.make_gap(KnowledgeGap.Status.OPEN)
        self.client.post(reverse("support:gap_dismiss", args=[gap.pk]))
        gap.refresh_from_db()
        self.assertEqual(gap.status, "dismissed")


class BenchmarkAndDashboardTests(ViewTestCase):
    def finished_run(self, name: str, arm: str, *, reused: int) -> BenchmarkRun:
        run = BenchmarkRun.objects.create(name=name, arm=arm, token_budget=10, datapack_id=str(uuid.uuid4()), status="done")
        for index in range(4):
            BenchmarkResult.objects.create(
                run=run, ticket_ref=f"T{index:03}", issue="payout", wave=1 if index == 0 else 2, source="reused" if index <= reused and index else "new",
                correct=True, keywords_total=2, keywords_found=2, input_tokens=4000 if index > reused else 2000, output_tokens=500,
                best_same_issue_score=1.0 if index else None,
            )
        return run

    def test_benchmark_page_without_runs_says_so(self):
        self.assertContains(self.client.get(reverse("support:benchmark")), "No finished benchmark yet")

    def test_benchmark_page_compares_the_latest_finished_runs(self):
        self.finished_run("D1", "docs_only", reused=0)
        self.finished_run("M1", "meko", reused=3)
        page = self.client.get(reverse("support:benchmark"))
        self.assertContains(page, "D1: docs only")
        self.assertContains(page, "M1: DejaFix + Meko")
        self.assertContains(page, "<td>Reused an approved fix</td><td>0</td><td>3</td>", html=True)
        self.assertContains(page, "Reuse threshold")
        self.assertContains(self.client.get(reverse("support:dashboard")), "Benchmark: same agents")

    def test_dashboard_shows_reuse_and_saved_tokens(self):
        self.resolved_ticket()
        self.client.post(reverse("support:resolution_approve", args=["T1"]))
        self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")
        page = self.client.get(reverse("support:dashboard"))
        tickets = page.context["tickets"]
        self.assertEqual((tickets.resolved, tickets.reused, tickets.human_touches), (2, 1, 1))
        self.assertGreater(tickets.tokens_saved, 0)
        self.assertContains(page, "(50%)")

    def test_dashboard_query_count_does_not_grow_with_data(self):
        self.client.get(reverse("support:dashboard"))  # warm the Meko quality cache
        # session, user, ticket statuses, resolution sums, gap statuses, gap counts, benchmark runs
        with self.assertNumQueries(7):
            self.client.get(reverse("support:dashboard"))
        for ref in ("T1", "T2", "T3"):
            self.resolved_ticket(ref)
        self.make_gap()
        self.finished_run("D1", "docs_only", reused=0)
        self.finished_run("M1", "meko", reused=2)
        self.client.get(reverse("support:dashboard"))
        # + 2 benchmark runs x 2 queries each (results, failed count)
        with self.assertNumQueries(11):
            self.client.get(reverse("support:dashboard"))

    def test_meko_context_quality_is_cached_and_failure_shows_unavailable(self):
        self.world.weak_searches = [WeakSearch(log_id="l1", query="q", agent_id="a", conversation_id="c", band="high")]
        self.assertContains(self.client.get(reverse("support:dashboard")), "100%")
        calls = self.world.events.count("meko:selflearning_status")
        self.client.get(reverse("support:dashboard"))
        self.assertEqual(self.world.events.count("meko:selflearning_status"), calls)
        cache.clear()
        self.world.fail_tool = "selflearning_status"
        with self.assertLogs("dejafix", "WARNING"):
            page = self.client.get(reverse("support:dashboard"))
        self.assertContains(page, "unavailable")


class LoginTests(TestCase):
    def test_login_page_logs_staff_in_and_goes_to_the_dashboard(self):
        get_user_model().objects.create_user("lead", password="a-long-test-password", is_staff=True)
        self.assertContains(self.client.get(reverse("login")), "Support staff log in")
        bad = self.client.post(reverse("login"), {"username": "lead", "password": "wrong"})
        self.assertContains(bad, "didn't match")
        response = self.client.post(reverse("login"), {"username": "lead", "password": "a-long-test-password"})
        self.assertRedirects(response, reverse("support:dashboard"), fetch_redirect_response=False)
