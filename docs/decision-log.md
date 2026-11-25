# Decision log

## R1: Project skeleton and Meko gateway (week 1)

- **Decision:** Django 5.2 LTS project. Meko is reached through our own async MCP gateway
  (`dejafix/meko/gateway.py`) built on the official `mcp` SDK, not through the model's MCP connector.
  - **Why:**
    - Meko identifies agents, conversations and datapacks by arguments, so code (not the model) must set them.
    - Calls are checked against the live schemas before sending, because Meko Cloud rejects unknown arguments.
    - Every call gets a timeout. Only read-only tools are retried, with backoff, so a timeout can't store the same memory twice.
    - Each call is recorded without its arguments, for our traces (A09).
- **Decision:** Build against the live tool schemas, saved by `manage.py meko_probe`, instead of the published docs.
  - **Why:** Meko Cloud publishes 57 tools. The docs miss several (`track_token_usage`, `selflearning_*`, `amp_*`).
  - Meko Cloud also requires `conversation_id` on calls whose schema marks it optional (for example, `datapack_list` answered with the in-band error `conversation_id_required`).
  - `conversation_create` requires a `datapack_id`, so the first datapack must be created in the portal.
- **Decision:** Secrets only from the environment (a stdlib `.env` loader, no new dependency).
  - `DEBUG` is off by default. `MekoConfig` hides the key from `repr`.
  - Plain HTTP is allowed only for a localhost Meko (A02, A04).
- **Rejected:**
  - The Anthropic MCP connector: the model would choose `agent_id`, and we'd lose per-call timeouts and records.
  - LangChain/Pydantic AI adapters: extra dependencies we don't need.
  - Self-hosting Meko: the README says Windows is not supported.
  - Django 6.x: 5.2 is the LTS release and supports Python 3.14.
- **Sources:**
  - [source] `mcp` 2.3.0 installed:
    - `mcp/client/client.py`: `Client`, `call_tool`, `list_tools(cursor=)`
    - `mcp/client/streamable_http.py`: `streamable_http_client(url, http_client=)`
    - `mcp_types/_types.py`: `CallToolResult.content`, `structured_content`, `is_error`; `ListToolsResult.next_cursor`
    - `mcp/server/mcpserver/server.py`: `MCPServer.tool`, used for the in-process test fake
  - [source] `httpx2` 2.16.2 installed: `HTTPStatusError(..., request=, response=)`, `AsyncClient`, `Timeout`
  - [read] Live Meko Cloud `tools/list` (saved to `docs/meko-tools.json`) and its in-band errors `conversation_id_required` and `datapack_id_required`
  - [read] github.com/yugabyte/meko `src/meko/docs/api.md`:
    - reply conventions: JSON as text, in-band `{"error","detail"}` with `isError: false`
    - Cloud rejects unknown arguments
  - [read] mekodata.ai/langchain and mekodata.ai/pydantic-ai: `Authorization: Bearer <api-key>` on `https://mcp.mekodata.ai/mcp`
  - [read] pip index: Django 5.2.19 is the latest 5.2 LTS patch
  - [knowledge] Django 5.2 supports Python 3.14 from 5.2.8. Verified in practice: the suite runs on 3.14.5.
  - [knowledge] OWASP Top 10:2025 A02 (misconfiguration), A04 (keys over TLS only), A09 (no secrets or arguments in logs)
