"""The redesigned staff UI: readable agent decisions, the pipeline stepper, the shared-memory page,
reuse lineage on tickets, and a CSP that allows only our own script file."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase
from django.urls import reverse

from support.middleware import CONTENT_SECURITY_POLICY
from support.models import AgentRun, Resolution, Ticket
from support.presenters import pipeline
from support.templatetags.dejafix_ui import decision_value, short_id
from support.tests.test_views import ViewTestCase

TEMPLATES = Path(settings.BASE_DIR) / "support" / "templates"
MEMORY_ID = "9adc8cce-d535-4f15-b07a-2d8f7e23eab6"


class DecisionValueTests(SimpleTestCase):
    def test_lists_become_bullets_and_text_is_escaped(self):
        html = decision_value(["Open Settings > Tax", "<script>alert(1)</script>"])
        self.assertIn('<ul class="dv-list">', html)
        self.assertIn("<li>Open Settings &gt; Tax</li>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_approved_fixes_become_score_chips_with_short_ids(self):
        html = decision_value([{"memory_id": MEMORY_ID, "score": 0.973}])
        self.assertIn("0.97", html)
        self.assertIn(f'title="{MEMORY_ID}"', html)
        self.assertIn("9adc8cce…", html)
        self.assertNotIn("{", html)

    def test_scalars_are_formatted(self):
        self.assertIn("none", decision_value([]))
        self.assertIn("—", decision_value(None))
        self.assertIn("yes", decision_value(True))
        self.assertIn("no", decision_value(False))
        self.assertIn("0.88", decision_value(0.88))
        self.assertIn("32,000", decision_value(32000))
        self.assertIn(f'title="{MEMORY_ID}"', decision_value(MEMORY_ID))
        self.assertEqual(decision_value("billing"), "billing")
        self.assertEqual(decision_value("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;")

    def test_short_id_keeps_short_values(self):
        self.assertEqual(short_id(MEMORY_ID), "9adc8cce…")
        self.assertEqual(short_id("abc"), "abc")
        self.assertEqual(short_id(""), "")


class TemplateHygieneTests(SimpleTestCase):
    def test_templates_have_no_inline_styles_or_scripts(self):
        # The CSP allows styles and scripts from our own files only.
        for path in TEMPLATES.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotRegex(text, r"\sstyle=")
                self.assertNotRegex(text, r"<script(?![^>]*\ssrc=)")
                self.assertNotRegex(text, r"\son[a-z]+=")

    def test_csp_allows_only_our_own_scripts(self):
        self.assertIn("script-src 'self'", CONTENT_SECURITY_POLICY)
        self.assertNotIn("unsafe-inline", CONTENT_SECURITY_POLICY)
        self.assertNotIn("http", CONTENT_SECURITY_POLICY)
        self.assertIsNotNone(finders.find("support/app.js"))


class PipelineTests(ViewTestCase):
    def stages(self, ticket: Ticket) -> dict[str, tuple[str, str]]:
        ticket.refresh_from_db()
        resolution = Resolution.objects.filter(ticket=ticket).first()
        return {stage.key: (stage.state, stage.detail) for stage in pipeline(ticket, resolution, list(ticket.runs.order_by("id")))}

    def test_new_ticket_is_all_pending(self):
        ticket = Ticket.objects.create(external_id="T9", subject="s", body="b", customer="c")
        stages = self.stages(ticket)
        self.assertEqual(list(stages), ["triage", "retrieve", "resolve", "review", "human"])
        self.assertEqual({state for state, _ in stages.values()}, {"pending"})

    def test_solved_ticket_waits_for_a_human_then_shows_the_decision(self):
        ticket = self.resolved_ticket()
        stages = self.stages(ticket)
        self.assertEqual([stages[k][0] for k in ("triage", "retrieve", "resolve", "review")], ["done"] * 4)
        self.assertEqual(stages["resolve"][1], "Solved from the documentation")
        self.assertEqual(stages["human"][0], "current")
        self.client.post(reverse("support:resolution_approve", args=[ticket.external_id]))
        self.assertEqual(self.stages(ticket)["human"], ("done", "Approved by lead"))

    def test_reused_ticket_needs_no_review_or_human(self):
        first = self.resolved_ticket()
        self.client.post(reverse("support:resolution_approve", args=[first.external_id]))
        second = self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")
        stages = self.stages(second)
        self.assertEqual(stages["resolve"][0], "done")
        self.assertIn("Reused an approved fix", stages["resolve"][1])
        self.assertEqual(stages["review"][0], "skipped")
        self.assertEqual(stages["human"][0], "skipped")


class TicketLineageTests(ViewTestCase):
    def test_reuse_links_both_ways(self):
        first = self.resolved_ticket()
        self.client.post(reverse("support:resolution_approve", args=[first.external_id]))
        self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")

        reused_page = self.client.get(reverse("support:ticket_detail", args=["T2"]))
        self.assertContains(reused_page, 'aria-label="Agent pipeline"')
        self.assertContains(reused_page, f'href="{reverse("support:ticket_detail", args=["T1"])}"')
        self.assertContains(reused_page, "approved on T1")

        source_page = self.client.get(reverse("support:ticket_detail", args=["T1"]))
        self.assertContains(source_page, "Reused by")
        self.assertContains(source_page, f'href="{reverse("support:ticket_detail", args=["T2"])}"')

    def test_decisions_render_without_python_reprs(self):
        ticket = self.resolved_ticket()
        page = self.client.get(reverse("support:ticket_detail", args=[ticket.external_id])).content.decode()
        self.assertNotRegex(page, r"\[\{'|\['|': ")

    def test_corrections_applied_are_shown_and_escaped(self):
        ticket = self.resolved_ticket()
        solve = AgentRun.objects.get(ticket=ticket, step="solve")
        solve.decision = {**solve.decision, "corrections_applied": ["Checks the <b>holiday</b> calendar first."]}
        solve.save(update_fields=["decision"])
        page = self.client.get(reverse("support:ticket_detail", args=[ticket.external_id]))
        self.assertContains(page, "Human corrections applied")
        self.assertContains(page, "Checks the &lt;b&gt;holiday&lt;/b&gt; calendar first.")

    def test_run_button_carries_the_busy_hook_and_works_without_javascript(self):
        Ticket.objects.create(external_id="T9", subject="s", body="b", customer="c")
        page = self.client.get(reverse("support:ticket_detail", args=["T9"]))
        self.assertContains(page, "data-busy")
        self.assertContains(page, 'src="/static/support/app.js"')
        self.assertContains(page, 'method="post" action="/tickets/T9/resolve/"')


class MemoryPageTests(ViewTestCase):
    URL = "/memory/"

    def approved_and_reused(self) -> None:
        first = self.resolved_ticket()
        self.client.post(reverse("support:resolution_approve", args=[first.external_id]))
        self.resolved_ticket("T2", body="Weekend bookings not paid out yet.")

    def test_url_and_access(self):
        self.assertEqual(reverse("support:memory"), self.URL)
        self.assertEqual(Client().get(self.URL).status_code, 302)
        plain = Client()
        plain.force_login(self.plain)
        self.assertEqual(plain.get(self.URL).status_code, 403)
        self.assertEqual(self.client.post(self.URL).status_code, 405)

    def test_empty_state(self):
        page = self.client.get(self.URL)
        self.assertContains(page, "No approved fixes yet")
        self.assertContains(page, "No corrections yet")

    def test_approved_fix_lists_its_reuses_and_tokens_saved(self):
        self.approved_and_reused()
        page = self.client.get(self.URL)
        overview = page.context["memory"]
        (fix,) = overview.fixes
        self.assertEqual(fix.ticket_ref, "T1")
        self.assertEqual([reuse.ticket_ref for reuse in fix.reuses], ["T2"])
        source_tokens = sum(run.total_tokens for run in AgentRun.objects.filter(ticket__external_id="T1"))
        reuse_tokens = sum(run.total_tokens for run in AgentRun.objects.filter(ticket__external_id="T2"))
        self.assertEqual(fix.tokens_saved, source_tokens - reuse_tokens)
        self.assertEqual((overview.total_reuses, overview.tokens_saved), (1, source_tokens - reuse_tokens))
        self.assertContains(page, f'href="{reverse("support:ticket_detail", args=["T2"])}"')
        self.assertContains(page, "Approved by lead")

    def test_correction_shows_the_human_note(self):
        ticket = self.resolved_ticket()
        self.client.post(reverse("support:resolution_reject", args=[ticket.external_id]), {"note": "Mention <the> Payouts calendar."})
        page = self.client.get(self.URL)
        self.assertContains(page, "Mention &lt;the&gt; Payouts calendar.")
        self.assertEqual(len(page.context["memory"].corrections), 1)

    def test_query_count_does_not_grow_with_data(self):
        self.approved_and_reused()
        # session, user, approved fixes, their reuses, token sums, corrections
        with self.assertNumQueries(6):
            self.client.get(self.URL)
        for ref in ("T3", "T4"):
            self.resolved_ticket(ref, body="Bookings from the weekend are still pending.")
        rejected = self.resolved_ticket("T5", body="Something else entirely about invoices.")
        if Resolution.objects.get(ticket=rejected).source == "new":
            self.client.post(reverse("support:resolution_reject", args=["T5"]), {"note": "Wrong."})
        with self.assertNumQueries(6):
            self.client.get(self.URL)

    def test_nav_links_to_the_memory_page(self):
        self.assertContains(self.client.get(reverse("support:dashboard")), f'href="{self.URL}"')


class NoReprRegexSanity(SimpleTestCase):
    def test_pattern_catches_a_python_list_repr(self):
        self.assertRegex("['a', 'b']", r"\[\{'|\['|': ")
        self.assertIsNone(re.search(r"\[\{'|\['|': ", "<li>a</li>"))
