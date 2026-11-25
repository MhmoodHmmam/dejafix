"""Async gateway to the Meko Context Engine over MCP (Streamable HTTP).

Why our own gateway instead of handing Meko's MCP server straight to the model:
- Meko identifies agents by an `agent_id` argument, so the code (not the model) must set it.
- Meko Cloud rejects unknown arguments, so calls are checked against the live tool schemas first.
- Every call gets a timeout, read-only calls get bounded retries, and each call is recorded
  (tool, agent, outcome, duration; never the arguments) for our own decision traces.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent, Tool

from dejafix.meko.config import MekoConfig
from dejafix.meko.errors import (
    MekoArgumentError,
    MekoAuthError,
    MekoError,
    MekoTimeout,
    MekoToolError,
    MekoUnavailable,
)

logger = logging.getLogger(__name__)

USER_AGENT = "dejafix/0.1"
AGENT_ID_FIELD = "agent_id"
# Meko Cloud nests every call under a conversation trace and rejects most calls without one,
# even where the schema marks it optional.
CONVERSATION_ID_FIELD = "conversation_id"
DATAPACK_ID_FIELD = "datapack_id"
MAX_READ_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.5
AUTH_FAILURE_STATUSES = frozenset({401, 403})
MAX_ERROR_DETAIL_CHARS = 300

# Tools that only read. They are safe to retry; writes are never retried, so a timeout
# can't store the same memory twice.
READ_ONLY_TOOLS = frozenset(
    {
        "context_search",
        "memory_search",
        "memory_get_all",
        "memory_get_by_id",
        "conversation_get",
        "conversation_list",
        "datapack_list",
        "datapack_search",
        "datapack_describe",
        "datapack_metrics",
        "project_list",
        "project_describe",
        "project_stats",
        "artifact_get",
        "knowledgebase_search",
        "knowledgebase_document_status",
        "selflearning_status",
        "selflearning_query_log",
        "selflearning_shortfalls",
        "amp_cluster_list",
        "amp_cluster_get",
        "amp_core_budget",
    }
)

_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (MCPError, httpx2.HTTPError, OSError, TimeoutError)

SleepFn = Callable[[float], Awaitable[Any]]


@dataclass(frozen=True)
class MekoCallRecord:
    """One Meko call, as kept in our trace. Arguments and replies are deliberately absent."""

    tool: str
    agent_id: str | None
    ok: bool
    duration_ms: float
    attempt: int
    error_code: str | None = None


CallObserver = Callable[[MekoCallRecord], None]


def _leaf_exceptions(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaf_exceptions(inner)
    else:
        yield exc


def _translate_connection_error(exc: BaseException) -> MekoError | None:
    """Map transport failures (possibly wrapped in exception groups) to gateway errors."""
    leaves = list(_leaf_exceptions(exc))
    for leaf in leaves:
        if isinstance(leaf, MekoError):
            return leaf
        if isinstance(leaf, httpx2.HTTPStatusError) and leaf.response.status_code in AUTH_FAILURE_STATUSES:
            return MekoAuthError(f"Meko rejected the API key (HTTP {leaf.response.status_code}).")
    if leaves and all(isinstance(leaf, _TRANSPORT_ERRORS) for leaf in leaves):
        names = ", ".join(sorted({type(leaf).__name__ for leaf in leaves}))
        return MekoUnavailable(f"Meko is unreachable ({names}).")
    return None


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_ERROR_DETAIL_CHARS else text[:MAX_ERROR_DETAIL_CHARS] + "..."


def parse_tool_result(tool: str, result: CallToolResult) -> Any:
    """Return the JSON reply of a Meko tool, raising MekoToolError for either error style.

    Meko replies with JSON as text content (mirrored in structuredContent as {"result": text}),
    and reports actionable errors in-band as {"error": code, "detail": text} with isError false.
    """
    text = "".join(block.text for block in result.content if isinstance(block, TextContent))
    if not text and isinstance(result.structured_content, Mapping):
        mirrored = result.structured_content.get("result")
        text = mirrored if isinstance(mirrored, str) else json.dumps(result.structured_content)
    if result.is_error:
        raise MekoToolError(tool, "tool_error", _truncate(text))
    try:
        payload = json.loads(text) if text else None
    except json.JSONDecodeError:
        return text
    if isinstance(payload, Mapping) and isinstance(payload.get("error"), str):
        raise MekoToolError(tool, payload["error"], _truncate(str(payload.get("detail", ""))))
    return payload


def build_arguments(
    tool: Tool, arguments: Mapping[str, Any], defaults: Mapping[str, str | None]
) -> dict[str, Any]:
    """Check arguments against the tool's input schema.

    `defaults` (agent_id, conversation_id) are added only where the tool accepts them and the
    caller did not pass its own value.
    """
    schema = tool.input_schema or {}
    properties: Mapping[str, Any] = schema.get("properties") or {}
    final = {name: value for name, value in arguments.items() if value is not None}
    for name, value in defaults.items():
        if value and name in properties and name not in final:
            final[name] = value

    unknown = sorted(set(final) - set(properties))
    if unknown and schema.get("additionalProperties") is not True:
        raise MekoArgumentError(f"{tool.name}: unknown argument(s) {unknown}; allowed {sorted(properties)}")
    missing = sorted(set(schema.get("required") or []) - set(final))
    if missing:
        raise MekoArgumentError(f"{tool.name}: missing required argument(s) {missing}")
    return final


class MekoGateway:
    """Calls Meko tools for one agent identity over an open MCP connection."""

    def __init__(
        self,
        client: Client,
        tools: Mapping[str, Tool],
        *,
        timeout_seconds: float,
        agent_id: str | None = None,
        conversation_id: str | None = None,
        datapack_id: str | None = None,
        observer: CallObserver | None = None,
        sleep: SleepFn = anyio.sleep,
    ) -> None:
        self._client = client
        self._tools = dict(tools)
        self._timeout_seconds = timeout_seconds
        self._observer = observer
        self._sleep = sleep
        self.agent_id = agent_id
        self.conversation_id = conversation_id
        self.datapack_id = datapack_id

    @property
    def tools(self) -> Mapping[str, Tool]:
        return self._tools

    def _copy(self, *, agent_id: str | None, conversation_id: str | None) -> MekoGateway:
        return MekoGateway(
            self._client,
            self._tools,
            timeout_seconds=self._timeout_seconds,
            agent_id=agent_id,
            conversation_id=conversation_id,
            datapack_id=self.datapack_id,
            observer=self._observer,
            sleep=self._sleep,
        )

    def as_agent(self, agent_id: str) -> MekoGateway:
        """Same connection, different agent identity (each support agent has its own)."""
        return self._copy(agent_id=agent_id, conversation_id=self.conversation_id)

    def in_conversation(self, conversation_id: str) -> MekoGateway:
        """Same connection and agent; calls are nested under this conversation's trace."""
        return self._copy(agent_id=self.agent_id, conversation_id=conversation_id)

    async def call(self, tool_name: str, **arguments: Any) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise MekoToolError(tool_name, "unknown_tool", "not published by this Meko server")
        defaults = {
            AGENT_ID_FIELD: self.agent_id,
            CONVERSATION_ID_FIELD: self.conversation_id,
            DATAPACK_ID_FIELD: self.datapack_id,
        }
        payload = build_arguments(tool, arguments, defaults)
        attempts = MAX_READ_ATTEMPTS if tool_name in READ_ONLY_TOOLS else 1

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                result = await self._call_once(tool_name, payload)
                reply = parse_tool_result(tool_name, result)
            except MekoUnavailable as exc:
                self._record(tool_name, started, attempt, error_code=type(exc).__name__)
                if attempt == attempts:
                    raise
                await self._sleep(RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1))
                continue
            except MekoError as exc:
                code = exc.code if isinstance(exc, MekoToolError) else type(exc).__name__
                self._record(tool_name, started, attempt, error_code=code)
                logger.warning("Meko call failed: tool=%s agent=%s code=%s", tool_name, self.agent_id, code)
                raise
            self._record(tool_name, started, attempt)
            return reply
        raise AssertionError("unreachable: the retry loop always returns or raises")

    async def _call_once(self, tool_name: str, payload: dict[str, Any]) -> CallToolResult:
        try:
            with anyio.fail_after(self._timeout_seconds):
                return await self._client.call_tool(tool_name, payload)
        except TimeoutError as exc:
            raise MekoTimeout(f"{tool_name} took longer than {self._timeout_seconds}s") from exc
        except _TRANSPORT_ERRORS as exc:
            translated = _translate_connection_error(exc)
            raise (translated or MekoUnavailable(f"{tool_name}: {type(exc).__name__}")) from exc

    def _record(self, tool: str, started: float, attempt: int, error_code: str | None = None) -> None:
        if self._observer is None:
            return
        self._observer(
            MekoCallRecord(
                tool=tool,
                agent_id=self.agent_id,
                ok=error_code is None,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                attempt=attempt,
                error_code=error_code,
            )
        )


class MekoSession:
    """Async context manager: opens the MCP connection, loads tool schemas, yields a gateway.

    `server` is for tests: pass an in-process MCP server instead of connecting over HTTP.
    """

    def __init__(
        self,
        config: MekoConfig,
        *,
        agent_id: str | None = None,
        observer: CallObserver | None = None,
        sleep: SleepFn = anyio.sleep,
        server: Any = None,
    ) -> None:
        self._config = config
        self._agent_id = agent_id
        self._observer = observer
        self._sleep = sleep
        self._server = server
        self._stack: AsyncExitStack | None = None

    def _target(self) -> Any:
        if self._server is not None:
            return self._server
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self._config.api_key}", "User-Agent": USER_AGENT},
            timeout=httpx2.Timeout(self._config.timeout_seconds),
        )
        return streamable_http_client(self._config.mcp_url, http_client=http_client)

    async def __aenter__(self) -> MekoGateway:
        self._stack = AsyncExitStack()
        try:
            client = await self._stack.enter_async_context(
                Client(self._target(), read_timeout_seconds=self._config.timeout_seconds)
            )
            with anyio.fail_after(self._config.timeout_seconds):
                tools = await self._list_all_tools(client)
        except Exception as exc:
            await self._close_quietly()
            if isinstance(exc, TimeoutError):
                raise MekoTimeout("Meko did not answer tools/list in time.") from exc
            translated = _translate_connection_error(exc)
            if translated is None:
                raise
            raise translated from exc
        return MekoGateway(
            client,
            tools,
            timeout_seconds=self._config.timeout_seconds,
            agent_id=self._agent_id,
            datapack_id=self._config.datapack_id or None,
            observer=self._observer,
            sleep=self._sleep,
        )

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if self._stack is None:
            return False
        try:
            return bool(await self._stack.__aexit__(exc_type, exc, tb))
        except BaseExceptionGroup as group:
            # The transport's task group wraps our own exception; let the original propagate.
            if exc is not None and list(_leaf_exceptions(group)) == [exc]:
                return False
            raise

    @staticmethod
    async def _list_all_tools(client: Client) -> dict[str, Tool]:
        tools: dict[str, Tool] = {}
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.update({tool.name: tool for tool in page.tools})
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def _close_quietly(self) -> None:
        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        except Exception as exc:  # the connection already failed; keep the original error
            logger.debug("Ignoring error while closing a failed Meko connection: %s", type(exc).__name__)
        finally:
            self._stack = None
