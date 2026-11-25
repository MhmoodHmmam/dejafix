from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from dejafix.meko import MekoSession
from dejafix.tests.fakes import TEST_DATAPACK_ID, build_fake_meko
from support.management.commands.meko_probe import Command

FAKE_SETTINGS = {
    "MEKO_MCP_URL": "https://meko.test/mcp",
    "MEKO_API_KEY": "test-key",
    "MEKO_TIMEOUT_SECONDS": 5.0,
    "MEKO_DATAPACK_ID": TEST_DATAPACK_ID,
}


@override_settings(**FAKE_SETTINGS)
class MekoProbeCommandTests(SimpleTestCase):
    def setUp(self) -> None:
        self.server, self.state = build_fake_meko()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output = Path(tmp.name) / "meko-tools.json"

    def run_probe(self, *args: str) -> str:
        stdout = StringIO()
        factory = lambda config: MekoSession(config, server=self.server)  # noqa: E731
        with mock.patch.object(Command, "session_factory", staticmethod(factory)):
            call_command("meko_probe", "--output", str(self.output), *args, stdout=stdout)
        return stdout.getvalue()

    def test_writes_sorted_tool_schemas(self):
        out = self.run_probe()
        tools = json.loads(self.output.read_text(encoding="utf-8"))
        names = [tool["name"] for tool in tools]
        self.assertEqual(names, sorted(names))
        search = next(tool for tool in tools if tool["name"] == "context_search")
        self.assertEqual(search["input_schema"]["required"], ["query"])
        self.assertIn(f"{len(tools)} tools saved", out)

    def test_without_smoke_makes_no_tool_calls(self):
        self.run_probe()
        self.assertEqual(self.state.calls, [])

    def test_smoke_opens_dedup_conversation_then_lists_datapacks_in_it(self):
        out = self.run_probe("--smoke")
        self.assertEqual(
            self.state.calls,
            [
                (
                    "conversation_create",
                    {
                        "title": "DejaFix probe",
                        "session_id": "dejafix-probe",
                        "agent_id": "dejafix-probe",
                        "datapack_id": TEST_DATAPACK_ID,
                    },
                ),
                ("datapack_list", {"conversation_id": "conv-dejafix-probe"}),
            ],
        )
        self.assertIn("object with keys datapacks", out)
        self.assertNotIn("support-kb", out)

    def test_smoke_failure_still_saves_schemas(self):
        async def broken_smoke(meko):
            return await meko.call("datapack_list")  # no conversation: the fake rejects it in-band

        with mock.patch.object(Command, "_smoke", staticmethod(broken_smoke)), self.assertLogs("dejafix", "WARNING"):
            with self.assertRaisesMessage(CommandError, "conversation_id_required"):
                self.run_probe("--smoke")
        self.assertTrue(self.output.exists())

    @override_settings(MEKO_DATAPACK_ID="")
    def test_smoke_without_datapack_explains_the_portal_step_and_calls_nothing(self):
        with self.assertRaisesMessage(CommandError, "--smoke needs MEKO_DATAPACK_ID"):
            self.run_probe("--smoke")
        self.assertEqual(self.state.calls, [])

    @override_settings(MEKO_API_KEY="")
    def test_missing_key_fails_with_clear_error_and_writes_nothing(self):
        with self.assertRaisesMessage(CommandError, "MEKO_API_KEY is not set"):
            self.run_probe()
        self.assertFalse(self.output.exists())
