# How DejaFix uses Meko

Meko is DejaFix's only memory. Each agent has its own Meko identity. Every step is a traced
conversation turn. Fixes and human corrections are memories. The documentation is Meko's
knowledge base, and the evidence behind each decision is a Meko artifact. Meko's self-learning
grades tell DejaFix which articles are missing. The app's own database can be a Postgres cluster
in the same Meko datapack.

All calls go through one gateway (`dejafix/meko/gateway.py`). It sets `agent_id` and
`conversation_id` in code, checks every call against Meko's live tool schemas
([meko-tools.json](meko-tools.json), saved by `manage.py meko_probe`), and retries reads only.

## Tools we call

### Traces: why each decision was made, and what it cost

| Tool | Where | What for |
|---|---|---|
| `conversation_create` | `dejafix/agents/trace.py` | One conversation per agent per ticket. A shared `run_id` (`dejafix:<ticket>`) groups all of a ticket's agents. A fixed `session_id` makes a re-run reconnect instead of duplicating. |
| `conversation_add_message` | `dejafix/agents/trace.py` | Every step (triage, retrieve, reuse check, solve, review, curate, correction) is a turn with `reasoning`, a 2–4 item `plan` and a fixed `seed`, so retries don't add duplicate turns. |
| `track_token_usage` | `dejafix/agents/trace.py` | Claude's input and output tokens, attached to the exact turn that spent them. |

### Shared memory: never solve it twice

| Tool | Where | What for |
|---|---|---|
| `context_search` | `dejafix/agents/retrieval.py` | Called before every solve. Returns human corrections (memories), past conversations and documentation, with Meko's match quality and token savings. |
| `knowledgebase_search` | `dejafix/agents/retrieval.py`, `pipeline.py`, `knowledge.py` | Documentation chunks **and promoted (collective) fixes**. Promoted memories come back here, not from `context_search`. Also used by the reviewer for policy and by `verify_gaps` to rank an article. |
| `memory_add` | `dejafix/agents/pipeline.py` | After a human approves: the curator stores the fix in a fixed, parseable format. After a rejection: the note becomes a correction memory. |
| `memory_promote` | `dejafix/agents/pipeline.py` | Turns an approved fix into the datapack's collective memory, so every resolver can reuse it. Corrections are not promoted: they guide, they don't answer. |

### Knowledge base

| Tool | Where | What for |
|---|---|---|
| `knowledgebase_upload_document` | `dejafix/agents/knowledge.py` | Seeds the LumenPay docs (`seed_knowledge`) and publishes gap articles. Meko returns a presigned upload URL; we accept HTTPS only. |
| `knowledgebase_document_status` | `dejafix/agents/knowledge.py` | Waits until a document is indexed before anything relies on it. |

### Self-learning: find and close our own gaps

| Tool | Where | What for |
|---|---|---|
| `selflearning_status` | `dejafix/agents/knowledge.py`, `support/dashboard.py` | Meko's live context quality on the dashboard (cached), and the list of open to-dos. |
| `selflearning_shortfalls` | `dejafix/agents/knowledge.py` | The weak searches behind the "add context" to-do, page by page. |
| `selflearning_query_log` | `dejafix/agents/knowledge.py` | The details of one weak search, so `find_gaps` keeps only our resolvers' searches and links each to its ticket. |
| `selflearning_verify` | `dejafix/agents/knowledge.py` | Replays a weak search after an article is published. We store the band before and after, what ranked first, and the article's rank. |

### Artifacts and datapacks

| Tool | Where | What for |
|---|---|---|
| `artifact_put` | `dejafix/agents/casefile.py` | On every human decision, a JSON case file (each step's reasoning, scores and tokens; no ticket text or names). The memory links to it with a `Case file: <sha256>` line, and we check that Meko's hash matches ours. Also stores the gap verification report. |
| `datapack_create` | `support/benchmark.py` | A fresh datapack per benchmark arm, so the two arms can't see each other's memory. |
| `datapack_list` | `support/management/commands/meko_probe.py` | A read-only connection check. |

### Postgres (AMP): the app's own database

DejaFix's Django state (tickets, resolutions, agent runs, gaps, benchmark results) can live in a
Postgres cluster that Meko manages in the same datapack. Setting `DEJAFIX_DB_HOST` switches Django
to it; without it, a local SQLite file is used (see [config/database.py](../config/database.py)).
`manage.py provision_db --allow-cidr <your IP>/32` creates the cluster and writes its settings to
`.env` (`support/management/commands/provision_db.py`, agent `dejafix-dba`). Run again, it shows the
cluster and fills in any missing endpoint settings, without creating a second one:

| Tool | What for |
|---|---|
| `amp_core_budget` | Checks that the free tier still has the 0.5 vCPU the endpoint needs. Otherwise nothing is created. |
| `amp_cluster_list` | Refuses to create a second cluster with the same name. |
| `amp_cluster_create` | Creates `dejafix-app` with a read-write endpoint at the free-tier size. The endpoint is public, so access is limited to the address you pass (no wider than /24). We send our own random password, written to `.env` before the call and never printed. |
| `amp_cluster_get` | On later runs: the existing cluster and its default branch. |
| `amp_compute_list` | The endpoint's address and login user, written to `.env`. |

Django connects with TLS required (`sslmode=require`, or a stricter mode), a 10 s connect timeout
and health-checked persistent connections. A half-configured database stops the app at startup
instead of silently falling back to SQLite.

Checked live: the whole test suite passed on this cluster (184 tests, 11 minutes, as every query
crosses the network). Each query costs a round trip of about 140 ms, so the dashboard uses 7
queries (it used 11) and loads in about 1 second. The first load after 5 minutes also waits about
4 seconds for Meko's live context quality, which is then cached.
`manage.py test` stays on SQLite unless `DEJAFIX_TEST_ON_POSTGRES=1`.

## How we use Meko's identities

- **`agent_id` is set by code, never by the model.** Each agent gets its own ID (see
  [architecture.md](architecture.md)), so Meko's traces show which agent did what.
- **Trust is checked on read.** Memory search spans every agent in the datapack. So a promoted fix
  is reused only if `dejafix-curator` wrote it in the fixed format, and a correction is used only if
  `dejafix-human-review` wrote it. Meko also auto-extracts memories from conversation turns; those
  never count as approved fixes.
- **Two resolvers, one memory.** In the benchmark, `dejafix-resolver-a` and `dejafix-resolver-b`
  take turns. In 7 of the 17 reuses, the fix had been approved on a ticket the other resolver solved.

## Results that depend on Meko

From the [benchmark](benchmark.md) (36 core tickets, same agents, docs only vs DejaFix + Meko,
two runs per arm, run 1 / run 2):

- Correct fixes on repeat tickets: 17 / 18 of 24 without shared memory, 22 / 19 with it.
- Tokens on repeat tickets: 5,348 / 5,329 vs 3,537 / 3,670 on average (−34% / −31%).
- Tickets a human had to handle: 36 / 36 vs 19 / 20.
- Reuse of another issue's fix: 0. Injection tickets reused or stored: 0 of 2.
- Six lookalike tickets (run 2), each worded like another issue: 4 of 6 correct from the docs, 6 of 6
  with shared memory. Five reused the fix for their real issue, none the fix they resemble.

From the self-learning loop (week 3, live): two weak searches went from Meko's "mid" band to
"high" after the librarian's articles were published (best score 0.68 → 0.78 and 0.69 → 0.87).
Each article ranked first in the knowledge base for its search. The top match overall was a past
conversation, which is why we record the article's own rank.

## What we learned about Meko Cloud

These were checked against the live service and are in the [decision log](decision-log.md):

- Most calls need a `conversation_id`, even where the schema marks it optional.
- Over MCP, `conversation_create` needs a datapack and `datapack_create` needs a conversation,
  so the first datapack is created in the portal.
- `datapack_create` names must be unique across the platform. Its `datapack_id` argument is
  only for trace routing.
- The self-learning query log writes conversation IDs as hyphenated UUIDs, while
  `conversation_create` returns 32 hex digits. We normalize both before matching.

## Tools we don't use, and why

| Tools | Why not |
|---|---|
| `memory_search`, `memory_get_all`, `memory_get_by_id` | `context_search` and `knowledgebase_search` already return what the resolver needs, with match quality. |
| `memory_update`, `memory_delete_by_id`, `memory_delete_all` | Memories are never edited. A better fix is a new approval; a wrong one gets a correction. That keeps the history auditable. |
| `conversation_get`, `conversation_list`, `conversation_update`, `conversation_delete` | We write traces; people read them in the Meko portal or on the ticket page. |
| `datapack_update`, `datapack_delete`, `datapack_search`, `datapack_describe` | One datapack per environment, plus one per benchmark arm. We never delete anything. |
| `artifact_get` | Case files are written for audit. They are read in the portal, not by the agents. |
| `project_create` and the other `project_*` tools | One datapack is enough for one support team. Projects would group several teams. |
| `amp_branch_create`, `amp_compute_create` and the other `amp_*` tools | One cluster and one endpoint are enough. Copy-on-write branches would suit the benchmark (one branch per arm), but that would mean re-running it. |
