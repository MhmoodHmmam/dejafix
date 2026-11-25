"""An in-process fake of Meko's MCP server: same reply conventions, no network."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anyio
from mcp.server.mcpserver import MCPServer

from dejafix.meko import MekoConfig

# The fake raises on purpose in some tools; its server-side traceback is expected noise.
logging.getLogger("mcp.server").setLevel(logging.CRITICAL)

TEST_DATAPACK_ID = "11111111-2222-4333-8444-555555555555"
TEST_CONFIG = MekoConfig(
    mcp_url="https://meko.test/mcp", api_key="test-key", timeout_seconds=0.2, datapack_id=TEST_DATAPACK_ID
)


@dataclass
class FakeMekoState:
    calls: list[tuple[str, dict]] = field(default_factory=list)
    memory_search_hangs_remaining: int = 0
    never: anyio.Event = field(default_factory=anyio.Event)


def build_fake_meko() -> tuple[MCPServer, FakeMekoState]:
    server = MCPServer("fake-meko")
    state = FakeMekoState()

    @server.tool()
    def context_search(
        query: str, limit: int = 5, agent_id: str | None = None, conversation_id: str | None = None
    ) -> str:
        state.calls.append(
            ("context_search", {"query": query, "limit": limit, "agent_id": agent_id, "conversation_id": conversation_id})
        )
        return json.dumps({"results": {"memory": [{"memory": f"fix for {query}"}]}, "agent_id": agent_id})

    @server.tool()
    def conversation_create(
        title: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        datapack_id: str | None = None,
    ) -> str:
        state.calls.append(
            (
                "conversation_create",
                {"title": title, "session_id": session_id, "agent_id": agent_id, "datapack_id": datapack_id},
            )
        )
        if datapack_id is None:  # mirrors Meko Cloud when the account has no default datapack
            return json.dumps({"error": "datapack_id_required", "detail": "Pass datapack_id (UUID)."})
        return json.dumps({"id": f"conv-{session_id or 'new'}", "agent_id": agent_id, "title": title})

    @server.tool()
    def memory_add(text: str, agent_id: str | None = None) -> str:
        state.calls.append(("memory_add", {"text": text, "agent_id": agent_id}))
        if text == "trigger-inband-error":
            return json.dumps({"error": "messages_missing", "detail": "no messages"})
        return json.dumps({"results": [{"id": "m1", "memory": text, "event": "ADD"}]})

    @server.tool()
    async def memory_search(query: str) -> str:
        state.calls.append(("memory_search", {"query": query}))
        if state.memory_search_hangs_remaining > 0:
            state.memory_search_hangs_remaining -= 1
            await state.never.wait()
        return json.dumps({"results": []})

    @server.tool()
    async def conversation_update(conversation_id: str, title: str) -> str:
        state.calls.append(("conversation_update", {"conversation_id": conversation_id, "title": title}))
        await state.never.wait()
        return "{}"

    @server.tool()
    def datapack_list(conversation_id: str | None = None) -> str:
        state.calls.append(("datapack_list", {"conversation_id": conversation_id}))
        if conversation_id is None:  # mirrors Meko Cloud's in-band rejection
            return json.dumps({"error": "conversation_id_required", "detail": "Pass conversation_id."})
        return json.dumps({"datapacks": [{"datapack_id": "dp1", "name": "support-kb"}]})

    @server.tool()
    def artifact_get(artifact_id: str) -> str:
        raise ValueError("storage exploded")

    @server.tool()
    def plain_text_tool() -> str:
        return "not json"

    return server, state
