"""Week 13: browse the recorded demo without API keys, and measure the reuse threshold's margin."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from dejafix.bench import BenchTicket, GroundTruth, MarginRow, reuse_margin
from dejafix.meko import MekoSession
from dejafix.tests.fakes import TEST_DATAPACK_ID
from dejafix.tests.stateful_meko import Memory, build_stateful_meko
from support.management.commands import reuse_margin as reuse_margin_command
from support.models import AgentRun, BenchmarkResult, BenchmarkRun, KnowledgeGap, Ticket

DEMO = Path(settings.BASE_DIR) / "data" / "demo.json"
NO_KEYS = {"MEKO_API_KEY": "", "ANTHROPIC_API_KEY": ""}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def demo_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in json.loads(DEMO.read_text(encoding="utf-8")):
        counts[item["model"]] = counts.get(item["model"], 0) + 1
    return counts


class DemoFixtureTests(SimpleTestCase):
    def test_demo_fixture_has_no_real_users_passwords_or_keys(self):
        text = DEMO.read_text(encoding="utf-8")
        items = json.loads(text)
        users = [item for item in items if item["model"] == "auth.user"]
        self.assertEqual(sorted(user["fields"]["username"] for user in users), ["demo-lead", "demo-reviewer"])
        for user in users:
            self.assertEqual(user["fields"]["password"], "!")  # unusable
            self.assertFalse(user["fields"]["is_superuser"] or user["fields"]["is_staff"])
        self.assertIsNone(EMAIL.search(text))
        for secret in ("sk-ant", "MEKO_API_KEY", "PASSWORD", "promoted_by"):
            self.assertNotIn(secret, text)
        self.assertFalse({item["model"] for item in items} & {"sessions.session", "admin.logentry"})


@override_settings(**NO_KEYS)
class DemoModeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_load_demo_fills_an_empty_database_and_refuses_a_full_one(self):
        out = StringIO()
        call_command("load_demo", stdout=out)
        counts = demo_counts()
        self.assertEqual(Ticket.objects.count(), counts["support.ticket"])
        self.assertEqual(AgentRun.objects.count(), counts["support.agentrun"])
        self.assertEqual(BenchmarkResult.objects.count(), counts["support.benchmarkresult"])
        self.assertIn("createsuperuser", out.getvalue())
        with self.assertRaisesMessage(CommandError, "--force"):
            call_command("load_demo", stdout=StringIO())
        call_command("load_demo", "--force", stdout=StringIO())
        self.assertEqual(Ticket.objects.count(), counts["support.ticket"])

    def test_demo_pages_render_without_any_meko_or_claude_call(self):
        call_command("load_demo", stdout=StringIO())
        self.client.force_login(get_user_model().objects.create_superuser("judge", password="unused-in-tests"))
        gap = KnowledgeGap.objects.order_by("pk").first()
        reused = Ticket.objects.filter(resolution__source="reused").order_by("pk").first()
        urls = ["/", "/tickets/", reverse("support:ticket_detail", args=[reused.external_id]), "/memory/", "/gaps/",
                reverse("support:gap_detail", args=[gap.pk]), "/benchmark/"]
        forbidden = mock.Mock(side_effect=AssertionError("no network call in demo mode"))
        with mock.patch("httpx2.AsyncClient", forbidden), mock.patch("anthropic.AsyncAnthropic", forbidden), \
             mock.patch.object(MekoSession, "__aenter__", forbidden):
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
            self.assertContains(self.client.get("/"), "unavailable")

    def test_run_without_keys_shows_a_clear_message_and_changes_nothing(self):
        user = get_user_model().objects.create_superuser("judge", password="unused-in-tests")
        self.client.force_login(user)
        ticket = Ticket.objects.create(external_id="T9", subject="s", body="b", customer="c")
        page = self.client.get(reverse("support:ticket_detail", args=["T9"]))
        self.assertContains(page, "CLAUDE_API_KEY and MEKO_API_KEY")
        self.assertNotContains(page, 'action="/tickets/T9/resolve/"')
        response = self.client.post(reverse("support:ticket_resolve", args=["T9"]))
        self.assertContains(response, "CLAUDE_API_KEY and MEKO_API_KEY", status_code=503)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.runs.count()), (Ticket.Status.NEW, 0))


class ReuseMarginLogicTests(SimpleTestCase):
    def test_margin_takes_the_best_wrong_issue_score(self):
        rows = [
            MarginRow(issue="payout", same_issue=0.99, other_issue=0.41, other_issue_name="fx"),
            MarginRow(issue="fx", same_issue=0.97, other_issue=0.62, other_issue_name="payout"),
            MarginRow(issue="sso", same_issue=0.95, other_issue=None, other_issue_name=""),
        ]
        summary = reuse_margin(rows, threshold=0.80)
        self.assertEqual((summary.max_other, summary.margin, summary.over_threshold), (0.62, 0.18, 0))
        self.assertEqual(summary.min_same, 0.95)
        self.assertEqual(reuse_margin(rows + [MarginRow("x", 0.9, 0.85, "y")], threshold=0.80).over_threshold, 1)
        self.assertIsNone(reuse_margin([MarginRow("a", 0.9, None, "")], threshold=0.8).max_other)


@override_settings(MEKO_API_KEY="test-key", MEKO_DATAPACK_ID=TEST_DATAPACK_ID, MEKO_MCP_URL="https://meko.test/mcp")
class ReuseMarginCommandTests(TestCase):
    def test_command_reports_the_margin_and_writes_nothing_to_memory(self):
        server, world = build_stateful_meko(docs=["Payouts settle T+2 business days."])
        run = BenchmarkRun.objects.create(name="M9", arm="meko", token_budget=10, datapack_id=str(uuid.uuid4()), status="done")
        for ref, issue, text in (
            ("T1", "payout", "DejaFix approved fix\nProblem: payout pending after the weekend\nFix: T+2 business days."),
            ("T2", "fx", "DejaFix approved fix\nProblem: payout smaller than sales in another currency\nFix: 1.5% fee."),
        ):
            memory = Memory(id=str(uuid.uuid4()), text=text, agent_id=run.curator_agent, promoted=True)
            world.memories.append(memory)
            BenchmarkResult.objects.create(run=run, ticket_ref=ref, issue=issue, wave=1, keywords_total=1, promoted_memory_id=memory.id,
                                           best_same_issue_score=0.99)
        BenchmarkResult.objects.create(run=run, ticket_ref="T3", issue="fx", wave=3, keywords_total=1, source="reused",
                                       correct=True, best_same_issue_score=0.97, best_other_issue_score=None)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        factory = lambda config, calls: MekoSession(config, observer=calls.append, server=server)  # noqa: E731
        truth = GroundTruth(
            issues={"payout": ("T+2",), "fx": ("1.5%",)},
            tickets=(
                BenchTicket("T1", "Where is my payout?", "Payout pending after the weekend.", "payout", 1, False),
                BenchTicket("T2", "Payout short", "Payout smaller than sales in another currency.", "fx", 1, False),
                BenchTicket("T3", "Weekend payout short", "Payout after the weekend is smaller, another currency.", "fx", 3, False,
                            lookalike_of="payout"),
            ),
        )
        with mock.patch.object(reuse_margin_command.Command, "session_factory", staticmethod(factory)),              mock.patch.object(reuse_margin_command, "ground_truth", lambda: truth):
            call_command("reuse_margin", "M9", "--output", str(folder / "margin.md"), stdout=StringIO())
        report = (folder / "margin.md").read_text(encoding="utf-8")
        self.assertIn("| T1 | payout |", report)
        self.assertIn("| T3 (lookalike of payout) | fx |", report)
        self.assertIn("holds its 2 human-approved fixes", report)
        self.assertIn("## What the resolver searched with during the run", report)
        self.assertIn("| T3 | 0.970 | - | yes | yes |", report)
        self.assertIn("Wrong fixes actually reused: 0.", report)
        self.assertIn("Highest score of another issue's fix", report)
        self.assertNotIn("@", report)  # Meko's promoted_by fields are never used
        writes = {"meko:memory_add", "meko:memory_promote", "meko:conversation_add_message", "meko:artifact_put",
                  "meko:knowledgebase_upload_document"}
        self.assertFalse(writes & set(world.events))
        self.assertIn("meko:memory_get_all", world.events)

    def test_unknown_or_docs_only_run_is_refused(self):
        BenchmarkRun.objects.create(name="D9", arm="docs_only", token_budget=10, datapack_id=str(uuid.uuid4()), status="done")
        for name in ("NOPE", "D9"):
            with self.subTest(name=name), self.assertRaises(CommandError):
                call_command("reuse_margin", name, stdout=StringIO())
