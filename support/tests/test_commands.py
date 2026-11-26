from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from dejafix.meko import MekoSession
from dejafix.tests.fake_llm import ScriptedLLM
from dejafix.tests.fakes import TEST_DATAPACK_ID
from dejafix.tests.stateful_meko import WORKFLOW_CONFIG, build_stateful_meko
from support.management.commands import resolve_tickets, seed_knowledge
from support.models import AgentRun, Resolution, Ticket

MEKO_SETTINGS = {"MEKO_API_KEY": "test-key", "MEKO_DATAPACK_ID": TEST_DATAPACK_ID, "MEKO_MCP_URL": "https://meko.test/mcp"}


def write_json(folder: str, document: object) -> Path:
    path = Path(folder) / "tickets.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class LoadTicketsTests(TestCase):
    def test_real_dataset_loads_42_tickets_and_rerun_is_idempotent(self):
        call_command("load_tickets", stdout=StringIO())
        call_command("load_tickets", stdout=StringIO())
        self.assertEqual(Ticket.objects.count(), 42)  # 36 core tickets + 6 lookalikes (week 9)
        self.assertEqual(Ticket.objects.filter(expected_issue="").count(), 0)
        arabic = Ticket.objects.get(external_id="T020")
        self.assertIn("العملاء", arabic.subject)

    def test_rerun_keeps_status_unless_reset(self):
        call_command("load_tickets", stdout=StringIO())
        ticket = Ticket.objects.get(external_id="T001")
        Ticket.objects.filter(pk=ticket.pk).update(status=Ticket.Status.CLOSED)
        AgentRun.objects.create(ticket=ticket, step="triage", agent="dejafix-triage")
        call_command("load_tickets", stdout=StringIO())
        self.assertEqual(Ticket.objects.get(pk=ticket.pk).status, Ticket.Status.CLOSED)
        call_command("load_tickets", "--reset", stdout=StringIO())
        self.assertEqual(Ticket.objects.get(pk=ticket.pk).status, Ticket.Status.NEW)
        self.assertFalse(AgentRun.objects.exists())

    def test_invalid_files_are_rejected_without_partial_writes(self):
        cases = {
            "not a list": {"tickets": "nope"},
            "missing body": {"tickets": [{"external_id": "X1", "subject": "s", "customer": "c"}]},
            "bad id": {"tickets": [{"external_id": "X 1; DROP", "subject": "s", "body": "b", "customer": "c"}]},
            "duplicate": {"tickets": [{"external_id": "X1", "subject": "s", "body": "b", "customer": "c"}] * 2},
            "too long": {"tickets": [{"external_id": "X1", "subject": "s" * 201, "body": "b", "customer": "c"}]},
        }
        with tempfile.TemporaryDirectory() as folder:
            for label, document in cases.items():
                with self.subTest(label), self.assertRaises(CommandError):
                    call_command("load_tickets", "--file", str(write_json(folder, document)), stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command("load_tickets", "--file", str(Path(folder) / "missing.json"), stdout=StringIO())
        self.assertFalse(Ticket.objects.exists())


@override_settings(**MEKO_SETTINGS)
class SeedKnowledgeTests(TestCase):
    def test_uploads_each_file_once_then_skips(self):
        server, world = build_stateful_meko()
        uploads: list[tuple[str, str]] = []

        async def fake_upload(url, fields, filename, content, content_type):
            uploads.append((url, filename))
            return 204

        factory = lambda config: MekoSession(config, server=server)  # noqa: E731
        with mock.patch.object(seed_knowledge.Command, "session_factory", staticmethod(factory)), \
             mock.patch.object(seed_knowledge.Command, "uploader", staticmethod(fake_upload)):
            call_command("seed_knowledge", stdout=StringIO())
            self.assertEqual(len(uploads), 9)
            self.assertTrue(all(url.startswith("https://") for url, _ in uploads))
            out = StringIO()
            call_command("seed_knowledge", stdout=out)
        self.assertEqual(len(uploads), 9)
        self.assertEqual(out.getvalue().count("already in Meko, skipped"), 9)

    async def test_presigned_upload_refuses_plain_http(self):
        with self.assertRaisesMessage(Exception, "insecure_upload_url"):
            await seed_knowledge.post_to_presigned_url("http://evil.test/upload", {}, "a.md", b"x", "text/markdown")


@override_settings(**MEKO_SETTINGS, DEJAFIX_CURATOR_AGENT="dejafix-curator")
class ResolveTicketsCommandTests(TestCase):
    def setUp(self):
        call_command("load_tickets", stdout=StringIO())
        self.server, self.world = build_stateful_meko()
        self.llm = ScriptedLLM(events=self.world.events)
        factory = lambda calls: MekoSession(WORKFLOW_CONFIG, observer=calls.append, server=self.server)  # noqa: E731
        patches = [
            mock.patch.object(resolve_tickets.Command, "llm_factory", staticmethod(lambda: self.llm)),
            mock.patch.object(resolve_tickets.Command, "session_factory", staticmethod(factory)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_resolves_requested_tickets_alternating_resolvers(self):
        out = StringIO()
        call_command("resolve_tickets", "T001", "T002", stdout=out)
        agents = list(Resolution.objects.order_by("ticket__external_id").values_list("resolver_agent", flat=True))
        self.assertEqual(agents, ["dejafix-resolver-a", "dejafix-resolver-b"])
        self.assertIn("2 ticket(s)", out.getvalue())

    def test_unknown_ticket_and_bad_limit_are_errors(self):
        with self.assertRaisesMessage(CommandError, "Unknown ticket(s): T999"):
            call_command("resolve_tickets", "T999", stdout=StringIO())
        for limit in ("0", "51"):
            with self.subTest(limit=limit), self.assertRaises(CommandError):
                call_command("resolve_tickets", "--limit", limit, stdout=StringIO())
        self.assertEqual(self.llm.calls, [])
