"""Behaviour of the Meko gateway against an in-process fake Meko MCP server."""

from __future__ import annotations

import httpx2
from django.test import SimpleTestCase

from dejafix.meko import (
    MekoArgumentError,
    MekoAuthError,
    MekoSession,
    MekoTimeout,
    MekoToolError,
    MekoUnavailable,
)
from dejafix.meko.gateway import RETRY_BASE_DELAY_SECONDS, _translate_connection_error
from dejafix.tests.fakes import TEST_CONFIG, TEST_DATAPACK_ID, build_fake_meko


class RecordingSleep:
    """Replaces anyio.sleep so retry back-off is asserted, not waited for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class MekoGatewayTests(SimpleTestCase):
    def setUp(self) -> None:
        self.server, self.state = build_fake_meko()
        self.sleep = RecordingSleep()
        self.records = []

    def session(self, agent_id: str | None = "resolver") -> MekoSession:
        return MekoSession(
            TEST_CONFIG, agent_id=agent_id, observer=self.records.append, sleep=self.sleep, server=self.server
        )

    async def test_loads_tool_schemas_and_parses_json_reply(self):
        async with self.session() as meko:
            self.assertIn("context_search", meko.tools)
            reply = await meko.call("context_search", query="login 500")
        self.assertEqual(reply["results"]["memory"][0]["memory"], "fix for login 500")

    async def test_agent_and_conversation_added_only_to_tools_that_accept_them(self):
        async with self.session(agent_id="triage") as meko:
            in_ticket = meko.in_conversation("conv-7")
            await in_ticket.call("context_search", query="q")
            await in_ticket.call("datapack_list")
        self.assertEqual(
            self.state.calls[0],
            ("context_search", {"query": "q", "limit": 5, "agent_id": "triage", "conversation_id": "conv-7"}),
        )
        # datapack_list takes a conversation but no agent_id: only the accepted default is sent.
        self.assertEqual(self.state.calls[1], ("datapack_list", {"conversation_id": "conv-7"}))

    async def test_configured_datapack_sent_to_tools_that_accept_it(self):
        async with self.session(agent_id="triage") as meko:
            await meko.call("conversation_create", title="Ticket 1")
        self.assertEqual(self.state.calls[0][1]["datapack_id"], TEST_DATAPACK_ID)

    async def test_in_conversation_keeps_agent_and_as_agent_keeps_conversation(self):
        async with self.session(agent_id="triage") as meko:
            curator = meko.in_conversation("conv-7").as_agent("curator")
        self.assertEqual((curator.agent_id, curator.conversation_id), ("curator", "conv-7"))

    async def test_as_agent_switches_identity_on_same_connection(self):
        async with self.session(agent_id="triage") as meko:
            await meko.as_agent("curator").call("memory_add", text="fix")
        self.assertEqual(self.state.calls[0][1]["agent_id"], "curator")

    async def test_explicit_agent_id_argument_is_not_overwritten(self):
        async with self.session(agent_id="triage") as meko:
            await meko.call("context_search", query="q", agent_id="human-reviewer")
        self.assertEqual(self.state.calls[0][1]["agent_id"], "human-reviewer")

    async def test_unknown_argument_rejected_before_sending(self):
        async with self.session() as meko:
            with self.assertRaisesMessage(MekoArgumentError, "unknown argument(s) ['colour']"):
                await meko.call("context_search", query="q", colour="red")
        self.assertEqual(self.state.calls, [])

    async def test_missing_required_argument_rejected_before_sending(self):
        async with self.session() as meko:
            with self.assertRaisesMessage(MekoArgumentError, "missing required argument(s) ['query']"):
                await meko.call("context_search")
        self.assertEqual(self.state.calls, [])

    async def test_none_arguments_are_dropped_not_sent(self):
        async with self.session() as meko:
            await meko.call("context_search", query="q", limit=None)
        self.assertEqual(self.state.calls[0][1]["limit"], 5)

    async def test_unknown_tool_raises_tool_error(self):
        async with self.session() as meko:
            with self.assertRaises(MekoToolError) as caught:
                await meko.call("memory_teleport")
        self.assertEqual(caught.exception.code, "unknown_tool")

    async def test_inband_error_reply_raises_with_meko_code(self):
        async with self.session() as meko:
            with self.assertRaises(MekoToolError) as caught, self.assertLogs("dejafix.meko.gateway", "WARNING"):
                await meko.call("memory_add", text="trigger-inband-error")
        self.assertEqual((caught.exception.tool, caught.exception.code), ("memory_add", "messages_missing"))
        self.assertEqual(self.records[-1].error_code, "messages_missing")

    async def test_is_error_result_raises_tool_error(self):
        async with self.session() as meko:
            with self.assertRaises(MekoToolError) as caught, self.assertLogs("dejafix.meko.gateway", "WARNING") as logs:
                await meko.call("artifact_get", artifact_id="a1")
        self.assertEqual(caught.exception.code, "tool_error")
        self.assertIn("tool=artifact_get agent=resolver code=tool_error", logs.output[0])

    async def test_non_json_reply_returned_as_text(self):
        async with self.session() as meko:
            self.assertEqual(await meko.call("plain_text_tool"), "not json")

    async def test_read_only_tool_retried_with_backoff_after_timeout(self):
        self.state.memory_search_hangs_remaining = 1
        async with self.session() as meko:
            reply = await meko.call("memory_search", query="q")
        self.assertEqual(reply, {"results": []})
        self.assertEqual(self.sleep.delays, [RETRY_BASE_DELAY_SECONDS])
        self.assertEqual([(r.attempt, r.ok) for r in self.records], [(1, False), (2, True)])

    async def test_read_only_tool_gives_up_after_max_attempts(self):
        self.state.memory_search_hangs_remaining = 99
        async with self.session() as meko:
            with self.assertRaises(MekoTimeout):
                await meko.call("memory_search", query="q")
        self.assertEqual(self.sleep.delays, [RETRY_BASE_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * 2])

    async def test_write_tool_never_retried_after_timeout(self):
        async with self.session() as meko:
            with self.assertRaises(MekoTimeout):
                await meko.call("conversation_update", conversation_id="c1", title="Ticket 1")
        self.assertEqual(len(self.state.calls), 1)
        self.assertEqual(self.sleep.delays, [])

    async def test_observer_records_outcome_without_arguments(self):
        async with self.session(agent_id="triage") as meko:
            await meko.call("context_search", query="secret customer text")
        record = self.records[0]
        self.assertEqual((record.tool, record.agent_id, record.ok, record.attempt), ("context_search", "triage", True, 1))
        self.assertNotIn("secret customer text", repr(record))

    async def test_exception_raised_inside_session_propagates_unchanged(self):
        with self.assertRaisesMessage(ValueError, "caller bug"):
            async with self.session():
                raise ValueError("caller bug")


class ConnectionErrorTranslationTests(SimpleTestCase):
    def _status_error(self, status: int) -> httpx2.HTTPStatusError:
        request = httpx2.Request("POST", "https://meko.test/mcp")
        return httpx2.HTTPStatusError("failed", request=request, response=httpx2.Response(status, request=request))

    def test_401_inside_exception_group_becomes_auth_error(self):
        translated = _translate_connection_error(ExceptionGroup("tg", [self._status_error(401)]))
        self.assertIsInstance(translated, MekoAuthError)

    def test_server_error_becomes_unavailable(self):
        self.assertIsInstance(_translate_connection_error(self._status_error(503)), MekoUnavailable)

    def test_connect_error_becomes_unavailable(self):
        self.assertIsInstance(_translate_connection_error(httpx2.ConnectError("refused")), MekoUnavailable)

    def test_programming_error_is_not_swallowed(self):
        self.assertIsNone(_translate_connection_error(ExceptionGroup("tg", [KeyError("bug")])))

    def test_auth_error_message_never_contains_the_key(self):
        translated = _translate_connection_error(self._status_error(401))
        self.assertNotIn(TEST_CONFIG.api_key, str(translated))
