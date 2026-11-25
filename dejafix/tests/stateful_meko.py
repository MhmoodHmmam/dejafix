"""A stateful in-process fake of Meko for workflow tests.

It mirrors what the live spike showed (docs/decision-log.md, R2):
- conversations are owned by one agent and deduplicated by session_id;
- memory_add stores per agent; memory_search/context_search see every agent's memories;
- memory_promote moves a memory out of memory search into the knowledge base, tagged
  source=memory with its writer's agent_id;
- context_search does NOT return promoted memories; knowledgebase_search does.
- selflearning_* report weak searches (`WeakSearch`); a replay scores the query against the
  documents uploaded since, like the live tool. artifact_put is idempotent by content hash.
Scores are word overlap (the share of query words found), which is enough for tests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer

from dejafix.meko import MekoConfig, MekoSession
from dejafix.tests.fakes import TEST_DATAPACK_ID

WORKFLOW_CONFIG = MekoConfig(
    mcp_url="https://meko.test/mcp", api_key="test-key", timeout_seconds=5.0, datapack_id=TEST_DATAPACK_ID
)
_WORD = re.compile(r"[a-z0-9+%./_-]{3,}")


def overlap(query: str, text: str) -> float:
    query_words = set(_WORD.findall(query.lower()))
    if not query_words:
        return 0.0
    return len(query_words & set(_WORD.findall(text.lower()))) / len(query_words)


@dataclass
class Memory:
    id: str
    text: str
    agent_id: str
    promoted: bool = False


HIGH_BAND_SCORE = 0.75  # the fake's stand-in for Meko's band thresholds


def band_for(score: float | None) -> str:
    if score is None or score < 0.4:
        return "low"
    return "high" if score >= HIGH_BAND_SCORE else "mid"


@dataclass
class WeakSearch:
    """A context_search that found too little, as listed by selflearning_shortfalls."""

    log_id: str
    query: str
    agent_id: str
    conversation_id: str
    band: str = "mid"
    best_score: float | None = 0.6
    reason: str = "partial_match"
    count: int = 1
    state: str = "open"
    test_prompt_id: str | None = None


@dataclass
class MekoWorld:
    """Everything the fake Meko stores, plus an ordered event log shared with the fake LLM."""

    events: list[str] = field(default_factory=list)
    conversations: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_usage: list[dict[str, Any]] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    uploaded_hashes: set[str] = field(default_factory=set)
    conversation_hits: list[dict[str, Any]] = field(default_factory=list)
    weak_searches: list[WeakSearch] = field(default_factory=list)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    indexing_keys: set[str] = field(default_factory=set)
    verify_calls: list[dict[str, str]] = field(default_factory=list)
    replay_top_source: str | None = None  # what Meko's replay ranked first (live: often "conversation")
    fail_tool: str | None = None
    fail_code: str = "internal_error"

    def memory_by_id(self, memory_id: str) -> Memory:
        return next(memory for memory in self.memories if memory.id == memory_id)

    def search_by_log_id(self, log_id: str) -> WeakSearch | None:
        return next((search for search in self.weak_searches if search.log_id == log_id), None)

    def session_factory(self, server: MCPServer):
        return lambda calls: MekoSession(WORKFLOW_CONFIG, observer=calls.append, server=server)

    async def receive_upload(self, url: str, fields: Any, filename: str, content: bytes, content_type: str) -> int:
        """Stands in for the presigned-URL POST: the document becomes searchable."""
        self.docs.append(content.decode("utf-8"))
        return 204


def build_stateful_meko(docs: list[str] | None = None) -> tuple[MCPServer, MekoWorld]:
    server = MCPServer("stateful-fake-meko")
    world = MekoWorld(docs=list(docs or []))

    def log(tool: str) -> str | None:
        world.events.append(f"meko:{tool}")
        if world.fail_tool == tool:
            return json.dumps({"error": world.fail_code, "detail": "simulated failure"})
        return None

    @server.tool()
    def conversation_create(
        agent_id: str | None = None,
        run_id: str | None = None,
        title: str | None = None,
        metadata: str | dict | None = None,  # the in-process server pre-parses JSON strings
        session_id: str | None = None,
        datapack_id: str | None = None,
    ) -> str:
        if failure := log("conversation_create"):
            return failure
        conversation_id = f"conv-{session_id}" if session_id else f"conv-{uuid.uuid4().hex}"
        world.conversations.setdefault(conversation_id, {"agent_id": agent_id, "run_id": run_id, "title": title})
        return json.dumps({"id": conversation_id, "agent_id": agent_id, "run_id": run_id, "title": title})

    @server.tool()
    def conversation_add_message(
        conversation_id: str,
        input: str,
        output: str,
        agent_id: str | None = None,
        reasoning: str | None = None,
        metadata: str | dict | None = None,  # the in-process server pre-parses JSON strings
        seed: str | None = None,
        datapack_id: str | None = None,
        plan: list[str] | None = None,
    ) -> str:
        if failure := log("conversation_add_message"):
            return failure
        owner = world.conversations.get(conversation_id, {}).get("agent_id")
        if owner != agent_id:
            return json.dumps({"error": "agent_id_mismatch", "detail": f"{agent_id} does not own the conversation"})
        message_id = seed or uuid.uuid4().hex
        world.messages[message_id] = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "input": input,
            "output": output,
            "reasoning": reasoning,
            "plan": plan,
            "metadata": json.loads(metadata) if isinstance(metadata, str) else metadata,
        }
        return json.dumps({"status": "accepted", "message_id": message_id, "conversation_id": conversation_id})

    @server.tool()
    def track_token_usage(
        conversation_id: str,
        name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
        model: str | None = None,
        message_id: str | None = None,
        datapack_id: str | None = None,
    ) -> str:
        if failure := log("track_token_usage"):
            return failure
        world.token_usage.append({"name": name, "input": input_tokens, "output": output_tokens, "message_id": message_id})
        return json.dumps({"recorded": True})

    @server.tool()
    def memory_add(
        text: str,
        conversation_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: str | dict | None = None,  # the in-process server pre-parses JSON strings
        messages: str | None = None,
        datapack_id: str | None = None,
    ) -> str:
        if failure := log("memory_add"):
            return failure
        memory = Memory(id=str(uuid.uuid4()), text=text, agent_id=agent_id or "meko_agent")
        world.memories.append(memory)
        return json.dumps({"results": [{"id": memory.id, "memory": text, "event": "ADD", "role": "user"}]})

    @server.tool()
    def memory_promote(
        conversation_id: str, memory_ids: list[str], agent_id: str | None = None, datapack_id: str | None = None
    ) -> str:
        if failure := log("memory_promote"):
            return failure
        found = [memory for memory in world.memories if memory.id in memory_ids and not memory.promoted]
        for memory in found:
            memory.promoted = True
        missing = [memory_id for memory_id in memory_ids if memory_id not in {m.id for m in found}]
        return json.dumps({"inserted_ids": [m.id for m in found], "updated_ids": [], "not_found_ids": missing})

    @server.tool()
    def context_search(
        query: str,
        conversation_id: str,
        agent_id: str | None = None,
        datapack_id: str | None = None,
        limit: int = 10,
        force_source: str = "auto",
        prompt: str | None = None,
    ) -> str:
        if failure := log("context_search"):
            return failure
        hits = sorted(
            ({"id": m.id, "memory": m.text, "agent_id": m.agent_id, "score": overlap(query, m.text)}
             for m in world.memories if not m.promoted),
            key=lambda hit: -hit["score"],
        )[:limit]
        quality = "high" if hits and hits[0]["score"] >= 0.8 else "low"
        return json.dumps(
            {
                "results": {
                    "knowledge_base": [],
                    "memory": hits,
                    "conversation": {"hits": world.conversation_hits, "thread": []},
                },
                "token_savings": {"total_tokens": 1000, "tokens_saved": 800},
                "match_quality": quality,
            }
        )

    @server.tool()
    def knowledgebase_search(
        query: str,
        conversation_id: str,
        datapack_id: str,
        agent_id: str | None = None,
        limit: int = 10,
        skip_conversation: bool = False,
    ) -> str:
        if failure := log("knowledgebase_search"):
            return failure
        chunks = [
            {"id": f"doc-{index}", "chunk_text": doc, "metadata_filters": {"source": "upload"}, "rerank_score": overlap(query, doc)}
            for index, doc in enumerate(world.docs)
        ] + [
            {
                "id": f"chunk-{m.id}",
                "chunk_text": m.text,
                "metadata_filters": {"source": "memory", "agent_id": m.agent_id, "memory_id": m.id},
                "rerank_score": overlap(query, m.text),
            }
            for m in world.memories
            if m.promoted
        ]
        # Like Meko's distance cutoff: chunks unrelated to the query are not returned.
        chunks = [chunk for chunk in chunks if chunk["rerank_score"] > 0]
        chunks.sort(key=lambda chunk: -chunk["rerank_score"])
        return json.dumps({"results": chunks[:limit], "count": len(chunks[:limit])})

    @server.tool()
    def knowledgebase_upload_document(
        filename: str,
        size: int,
        content_hash: str,
        conversation_id: str,
        content_head_base64: str,
        datapack_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        if failure := log("knowledgebase_upload_document"):
            return failure
        if content_hash in world.uploaded_hashes:
            return json.dumps({"datapack_id": datapack_id, "url": "", "key": filename, "already_uploaded": True})
        world.uploaded_hashes.add(content_hash)
        return json.dumps(
            {"datapack_id": datapack_id, "url": "https://s3.test/upload", "fields": {"key": filename, "policy": "p"},
             "key": filename, "already_uploaded": False}
        )

    @server.tool()
    def knowledgebase_document_status(
        document_key: str, conversation_id: str, datapack_id: str | None = None, agent_id: str | None = None
    ) -> str:
        if failure := log("knowledgebase_document_status"):
            return failure
        state = "processing" if document_key in world.indexing_keys else "completed"
        return json.dumps({"document_key": document_key, "state": state})

    @server.tool()
    def selflearning_status(datapack_id: str = "", last: str = "", conversation_id: str = "") -> str:
        if failure := log("selflearning_status"):
            return failure
        open_searches = [search for search in world.weak_searches if search.state != "verified"]
        high = sum(1 for search in world.weak_searches if search.band == "high")
        total = max(len(world.weak_searches), 1)
        return json.dumps(
            {
                "quality": {"pct": round(100 * high / total), "searches": len(world.weak_searches), "high": high},
                "todos": [{"id": "add_context", "state": "todo" if open_searches else "done", "searches": len(open_searches)}],
                "open_fixes": ["add_context"] if open_searches else [],
            }
        )

    @server.tool()
    def selflearning_shortfalls(
        todo_id: str, datapack_id: str = "", page: int = 1, last: str = "", conversation_id: str = ""
    ) -> str:
        if failure := log("selflearning_shortfalls"):
            return failure
        rows = [
            {
                "log_id": s.log_id, "query": s.query, "count": s.count, "band": s.band, "best_score": s.best_score,
                "reason": s.reason, "state": s.state, "test_prompt_id": s.test_prompt_id,
            }
            for s in world.weak_searches
        ] if todo_id == "add_context" else []
        start = (page - 1) * 10
        return json.dumps(
            {"todo": todo_id, "page": page, "total": len(rows), "has_more": start + 10 < len(rows), "searches": rows[start:start + 10]}
        )

    @server.tool()
    def selflearning_query_log(
        datapack_id: str = "", band: str = "low", reason: str = "", category: str = "", log_id: str = "",
        page: int = 1, last: str = "7d", conversation_id: str = "",
    ) -> str:
        if failure := log("selflearning_query_log"):
            return failure
        search = world.search_by_log_id(log_id)
        if search is None:
            return json.dumps({"error": "not_found", "detail": "no such search"})
        return json.dumps(
            {
                "id": search.log_id, "query": search.query, "band": search.band, "best_score": search.best_score,
                "reason": search.reason, "agent_id": search.agent_id, "conversation_id": search.conversation_id,
                "results": [],
            }
        )

    @server.tool()
    def selflearning_verify(
        datapack_id: str = "", log_id: str = "", test_prompt_id: str = "", conversation_id: str = ""
    ) -> str:
        if failure := log("selflearning_verify"):
            return failure
        world.verify_calls.append({"log_id": log_id, "test_prompt_id": test_prompt_id})
        search = next(
            (s for s in world.weak_searches if (log_id and s.log_id == log_id) or (test_prompt_id and s.test_prompt_id == test_prompt_id)),
            None,
        )
        if search is None:
            return json.dumps({"error": "not_found", "detail": "no such search"})
        before = {"band": search.band, "best_score": search.best_score}
        best = max((overlap(search.query, doc) for doc in world.docs), default=0.0)
        search.test_prompt_id = search.test_prompt_id or f"tp-{search.log_id}"
        search.band, search.best_score = band_for(best), round(best, 4)
        search.state = "verified" if search.band == "high" else "unverified"
        # Shape of the live reply (decision log, R3); top_results "s" names each hit's source.
        top_source = world.replay_top_source or ("knowledge_base" if best > 0 else "conversation")
        return json.dumps(
            {"test_prompt_id": search.test_prompt_id, "query": search.query, "before": before,
             "after": {"band": search.band, "best_score": search.best_score},
             "outcome": "improved" if search.band != before["band"] else "no_change",
             "nothing_added_since_last_run": False, "indexing": False,
             "top_results": [{"s": top_source, "score": best, "kind": "document"}]}
        )

    @server.tool()
    def artifact_put(
        filename: str, content_base64: str, content_type: str, conversation_id: str,
        datapack_id: str | None = None, agent_id: str | None = None,
    ) -> str:
        if failure := log("artifact_put"):
            return failure
        content = base64.b64decode(content_base64)
        content_hash = hashlib.sha256(content).hexdigest()
        created = content_hash not in world.artifacts
        world.artifacts.setdefault(
            content_hash, {"filename": filename, "content": content, "content_type": content_type, "agent_id": agent_id}
        )
        return json.dumps({"content_hash": content_hash, "filename": filename, "size": len(content), "created": created})

    return server, world
