"""Week 14: the agents run in the background with real step-by-step progress; login is throttled."""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dejafix.llm import LLMUnavailable
from support import progress, runner, views
from support.models import AgentRun, Resolution, Ticket
from support.tests.test_views import ViewTestCase


class BackgroundRunTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.ticket = Ticket.objects.create(external_id="T9", subject="Where is my payout?", body="Pending since Friday.", customer="Shop")
        self.run_url = reverse("support:ticket_resolve", args=["T9"])
        self.progress_url = reverse("support:ticket_progress", args=["T9"])

    @override_settings(DEJAFIX_RUN_AGENTS_INLINE=False)
    def test_run_claims_the_ticket_returns_at_once_and_a_second_start_is_409(self):
        with mock.patch.object(runner, "submit") as submit, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.run_url)
        self.assertRedirects(response, reverse("support:ticket_detail", args=["T9"]))
        self.assertEqual(Ticket.objects.get(pk=self.ticket.pk).status, Ticket.Status.RUNNING)
        submit.assert_called_once()
        self.assertFalse(AgentRun.objects.exists())  # nothing ran inside the request
        second = self.client.post(self.run_url)
        self.assertContains(second, "already working on this ticket", status_code=409)

    def test_background_job_runs_the_agents_and_reports_each_step(self):
        self.assertTrue(runner.claim(self.ticket))
        runner.run_job(self.ticket.pk, llm_factory=lambda: self.llm, session_factory=self.factory)
        self.assertEqual(Ticket.objects.get(pk=self.ticket.pk).status, Ticket.Status.AWAITING_REVIEW)
        data = self.client.get(self.progress_url).json()
        self.assertEqual((data["status"], data["done"]), ("awaiting_review", True))
        self.assertEqual([step["step"] for step in data["steps"]], ["triage", "retrieve", "solve", "review"])
        self.assertEqual(data["steps"][0]["tokens"], AgentRun.objects.get(ticket=self.ticket, step="triage").total_tokens)

    def test_progress_while_running_shows_only_finished_steps(self):
        self.assertTrue(runner.claim(self.ticket))
        progress.add_step(self.ticket.pk, step="triage", label="Triage", agent="dejafix-triage", tokens=1080, ms=2562, meko_calls=3)
        data = self.client.get(self.progress_url).json()
        self.assertEqual((data["status"], data["done"]), ("running", False))
        self.assertEqual(data["steps"], [{"step": "triage", "label": "Triage", "agent": "dejafix-triage", "tokens": 1080, "ms": 2562, "meko_calls": 3}])

    def test_progress_endpoint_is_staff_only_get_only_and_has_no_ticket_text(self):
        self.assertTrue(runner.claim(self.ticket))
        self.assertEqual(Client().get(self.progress_url).status_code, 302)
        plain = Client()
        plain.force_login(self.plain)
        self.assertEqual(plain.get(self.progress_url).status_code, 403)
        self.assertEqual(self.client.post(self.progress_url).status_code, 405)
        self.assertEqual(self.client.get(reverse("support:ticket_progress", args=["NOPE"])).status_code, 404)
        body = self.client.get(self.progress_url).content.decode()
        for text in ("Where is my payout", "Pending since Friday", "Shop"):
            self.assertNotIn(text, body)

    def test_running_page_shows_live_progress_and_refreshes_without_javascript(self):
        self.assertTrue(runner.claim(self.ticket))
        page = self.client.get(reverse("support:ticket_detail", args=["T9"]))
        self.assertContains(page, f'data-progress-url="{self.progress_url}"')
        self.assertContains(page, '<noscript><meta http-equiv="refresh" content="5"></noscript>', html=False)
        self.assertNotContains(page, f'action="{self.run_url}"')
        self.assertContains(page, "The agents are working on this ticket")

    def test_failed_background_run_marks_the_ticket_failed_and_stores_nothing(self):
        self.assertTrue(runner.claim(self.ticket))
        self.llm.fail_with = LLMUnavailable("down")
        with self.assertLogs("dejafix", "WARNING"):
            runner.run_job(self.ticket.pk, llm_factory=lambda: self.llm, session_factory=self.factory)
        ticket = Ticket.objects.get(pk=self.ticket.pk)
        self.assertEqual(ticket.status, Ticket.Status.FAILED)
        self.assertFalse(AgentRun.objects.exists() or Resolution.objects.exists())
        data = self.client.get(self.progress_url).json()
        self.assertEqual((data["status"], data["done"]), ("failed", True))

    def test_job_that_cannot_build_claude_marks_the_ticket_failed(self):
        self.assertTrue(runner.claim(self.ticket))

        def no_claude():
            raise LLMUnavailable("no key")

        with self.assertLogs("dejafix", "WARNING"):
            runner.run_job(self.ticket.pk, llm_factory=no_claude, session_factory=self.factory)
        self.assertEqual(Ticket.objects.get(pk=self.ticket.pk).status, Ticket.Status.FAILED)

    def test_stale_running_ticket_can_be_run_again(self):
        self.assertTrue(runner.claim(self.ticket))
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_resolvable)
        self.assertFalse(runner.claim(self.ticket))
        Ticket.objects.filter(pk=self.ticket.pk).update(updated_at=timezone.now() - timedelta(minutes=6))
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_resolvable)
        self.assertTrue(runner.claim(self.ticket))

    def test_runner_closes_its_database_connections(self):
        self.assertTrue(runner.claim(self.ticket))
        with mock.patch.object(runner, "close_old_connections") as close:
            runner.background_job(self.ticket.pk, lambda: self.llm)
        close.assert_called_once()
        self.assertEqual(Ticket.objects.get(pk=self.ticket.pk).status, Ticket.Status.AWAITING_REVIEW)

    def test_inline_mode_keeps_the_old_request_flow(self):
        # Tests and management commands run the agents in the request, as before.
        self.assertRedirects(self.client.post(self.run_url), reverse("support:ticket_detail", args=["T9"]))
        self.assertEqual(Ticket.objects.get(pk=self.ticket.pk).status, Ticket.Status.AWAITING_REVIEW)
        self.assertIs(views.llm_factory(), self.llm)


class LoginThrottleTests(TestCase):
    URL = "/login/"

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_user("lead", password="the-right-password-123", is_staff=True)

    def setUp(self):
        cache.clear()

    def fail(self, username: str = "lead"):
        return self.client.post(self.URL, {"username": username, "password": "wrong"})

    def test_login_is_throttled_after_five_failures_even_with_the_right_password(self):
        for _ in range(5):
            self.assertEqual(self.fail().status_code, 200)
        blocked = self.client.post(self.URL, {"username": "lead", "password": "the-right-password-123"})
        self.assertContains(blocked, "Too many failed attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_throttle_message_does_not_reveal_whether_the_user_exists(self):
        for _ in range(5):
            self.fail("lead")
            self.fail("nobody")
        known, unknown = self.fail("lead"), self.fail("nobody")
        self.assertEqual((known.status_code, unknown.status_code), (429, 429))
        def page(response, username):
            text = response.content.decode().replace(username, "X")
            return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", text)  # differs on every page

        self.assertEqual(page(known, "lead"), page(unknown, "nobody"))

    def test_a_successful_login_clears_the_count(self):
        for _ in range(4):
            self.fail()
        self.assertEqual(self.client.post(self.URL, {"username": "lead", "password": "the-right-password-123"}).status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.fail().status_code, 200)
        self.assertEqual(self.fail().status_code, 200)  # the 5th failure is still answered; the 6th is not
        self.assertEqual(self.fail().status_code, 429)
