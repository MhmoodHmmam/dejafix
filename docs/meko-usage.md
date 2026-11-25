# How DejaFix uses Meko

Meko is DejaFix's only memory. Each agent has its own Meko identity. Every step is a traced
conversation turn. Fixes and human corrections are memories. The documentation is Meko's
knowledge base, and the evidence behind each decision is a Meko artifact. Meko's self-learning
grades tell DejaFix which articles are missing.

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

From the [benchmark](benchmark.md) (36 tickets, same agents, docs only vs DejaFix + Meko):

- Correct fixes on repeat tickets: 17/24 without shared memory, 22/24 with it.
- Tokens on repeat tickets: 5,348 vs 3,537 on average (−34%).
- Tickets a human had to handle: 36 vs 19.
- Reuse of another issue's fix: 0. Injection tickets reused or stored: 0 of 2.

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
| `amp_cluster_create` and the other `amp_*` tools | These manage YugabyteDB clusters. DejaFix stores its own small state in SQLite locally. Moving that state to a Meko-managed Postgres cluster is a possible next step, not done. |
