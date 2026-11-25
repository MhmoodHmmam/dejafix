# DejaFix: never solve the same ticket twice

A multi-agent support team built on the [Meko Context Engine](https://mekodata.ai) for
**ContextCon: Remember Everything**, Challenge 02 "Never Solve It Twice".

Before solving a ticket, each agent searches Meko's shared memory and knowledge base.
Verified fixes become collective memory, and human corrections become learnings. Every
decision leaves a trace that explains *why*.

> Status: week 4. Four agents, human review in the Django admin, a self-learning loop on Meko's
> `selflearning_*` tools, case files stored as Meko artifacts, and a benchmark with and without
> shared memory. The dashboard comes next.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in the keys
```

`.env` needs:

| Variable | What |
|---|---|
| `CLAUDE_API_KEY` | Anthropic API key |
| `MEKO_API_KEY` | Meko Cloud API key |
| `MEKO_DATAPACK_ID` | UUID of a datapack you created in the Meko Cloud portal |

The datapack must be created in the portal: over MCP, `conversation_create` needs a datapack
and `datapack_create` needs a conversation.

## Check the Meko connection

```powershell
.\.venv\Scripts\python.exe manage.py meko_probe --smoke
```

This lists Meko's tools, saves their live input schemas to `docs/meko-tools.json`, and calls
the read-only `datapack_list` inside a reusable probe conversation.

## Run it

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py load_tickets          # 36 synthetic tickets
.\.venv\Scripts\python.exe manage.py seed_knowledge --wait # docs -> Meko knowledge base
.\.venv\Scripts\python.exe manage.py resolve_tickets T001  # calls Claude + Meko
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver            # review at /admin/
```

Approving a fix in the admin (Resolutions > Approve) stores it as the curator's memory and
promotes it to the datapack's collective memory. Repeat tickets then reuse it.

## How a ticket flows

1. **Triage** (`dejafix-triage`): category, an English problem signature, and an injection flag.
2. **Retrieve** (resolver): `context_search` plus `knowledgebase_search`, always before solving.
3. **Resolve**:
   - If a curator-approved fix scores 0.80 or more, a low-effort check decides whether it applies.
   - Otherwise the resolver solves from the documentation.
4. **Review** (`dejafix-reviewer`): checks the fix against the policy and the resolver's evidence.
   Code rules can only make the verdict stricter.
5. **Human** (Django admin):
   - Approve: the curator promotes the fix to collective memory.
   - Reject with a note: the note becomes a learning the next resolver reads.

Every step is a Meko conversation turn with `reasoning`, `plan` and token usage (`track_token_usage`).
Every human decision also stores a **case file** (the agents' steps, reasoning and scores, with no
ticket text or names) as a Meko artifact. The shared memory links to it (`Case file: <sha256>`).

## The self-learning loop

Meko grades every `context_search`. DejaFix uses those grades to find and close its own knowledge gaps:

```powershell
.\.venv\Scripts\python.exe manage.py find_gaps            # weak searches by our resolvers (read-only)
.\.venv\Scripts\python.exe manage.py draft_gap_articles   # librarian drafts from human-approved fixes (calls Claude)
# admin: Knowledge gaps > edit the draft > "Publish to the Meko knowledge base"
.\.venv\Scripts\python.exe manage.py verify_gaps          # replay each search: band before -> after
```

`verify_gaps` also records what the replay ranked first and where the article ranks in the
knowledge base. Meko's grade counts past conversations too, so a better band alone doesn't prove
the article helped.

## Benchmark: with and without shared memory

The same agents run over all 36 tickets twice, each arm in its own fresh datapack with the same
documentation. The only difference is Meko's shared memory. Results: [docs/benchmark.md](docs/benchmark.md).

```powershell
.\.venv\Scripts\python.exe manage.py benchmark_setup D1 --arm docs_only   # creates and seeds a datapack
.\.venv\Scripts\python.exe manage.py benchmark_setup M1 --arm meko
.\.venv\Scripts\python.exe manage.py benchmark D1                         # resumable; --limit N for batches
.\.venv\Scripts\python.exe manage.py benchmark M1
.\.venv\Scripts\python.exe manage.py benchmark_report D1 M1               # writes docs/benchmark.md
```

- **Grading:** each issue has ground-truth keywords in `data/tickets.json`. The agents never see them.
- **Simulated reviewer:** in the Meko arm, a reviewer approves or rejects each new fix right away. Its
  rejection notes name the missing keywords, so it is a kinder reviewer than a busy human.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test
```

Tests never touch the network. They run against an in-process fake of Meko's MCP server
(`dejafix/tests/fakes.py`) that follows the real server's reply conventions.

## Layout

| Path | Purpose |
|---|---|
| `dejafix/meko/` | Meko gateway: MCP connection, schema-checked calls, agent/conversation/datapack scoping, timeouts, retries for reads only, call records |
| `dejafix/agents/` | Agents: schemas, prompts, retrieval, traces, workflow, self-learning (`knowledge.py`), case files (`casefile.py`) |
| `dejafix/llm.py` | Claude wrapper: typed output, effort, refusal fallback, usage |
| `dejafix/bench.py` | Benchmark grading, simulated reviewer, reuse-threshold calibration |
| `support/` | Django app: models, services, admin review, management commands |
| `data/` | Synthetic LumenPay knowledge base and tickets |
| `docs/decision-log.md` | Decisions and their sources |
| `docs/meko-tools.json` | Live Meko tool schemas (generated by `meko_probe`) |
