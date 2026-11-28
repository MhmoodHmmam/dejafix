# Submission text (ready to paste)

For the HackerEarth form of **ContextCon: Remember Everything**, Challenge 02 "Never Solve It Twice".
Every number below comes from [benchmark.md](benchmark.md) or from live runs recorded in the
[decision log](decision-log.md). Replace `VIDEO_URL` once the video is uploaded.

## Project name

DejaFix

## Tagline

Never solve the same ticket twice: Claude support agents that share one memory in Meko, where only
human-approved fixes are remembered.

## Short description

Support teams solve the same problem again and again, because every agent, human or AI, starts from
zero. DejaFix is a team of Claude agents (triage, resolver, reviewer, curator, librarian) that share
one memory in Meko. Before solving anything, the resolver searches Meko for fixes a human approved,
human corrections, and the documentation. A repeat problem reuses an approved fix at about half the
tokens, with no human needed. A fix becomes collective memory only after a person approves it, and a
rejection note becomes a learning the next resolver must apply. Every step is a traced Meko
conversation turn, every human decision leaves a case file as a Meko artifact, and Meko's
self-learning grades show which help articles are missing.

## Results

Same agents, same 36 synthetic tickets, each arm in its own fresh Meko datapack: two docs-only runs
and three Meko runs (run 1 / 2 / 3).

| | Docs only | DejaFix + Meko shared memory |
|---|---|---|
| Correct fixes, of 36 | 25 / 26 | **30 / 28 / 30** |
| Correct on repeat tickets, of 24 | 17 / 18 | **22 / 19 / 21** |
| Avg tokens on repeat tickets | 5,348 / 5,329 | **3,537 / 3,670 / 3,681** (about −31% to −34%) |
| Tickets a human had to handle, of 36 | 36 / 36 | **19 / 20 / 19** |
| Next ticket correct after a human correction | – | 3 of 3 / 1 of 4 / **2 of 3** |
| Lookalike tickets correct, of 6 (runs 2 and 3) | 4 | **6 / 6** |
| Wrong reuse / injection tickets stored | 0 / 0 of 2 | 0 / 0 of 2 |

**Honest limits:**
- The reviewer in the benchmark is simulated, and kinder than a busy human.
- There are two or three runs per arm, so the report shows every run instead of one average.
- The reuse threshold alone would not stop a lookalike: searched with raw ticket text, one wrong fix
  scored 0.90 against a 0.80 threshold. The triage signature and the reuse check are the guard, and
  no wrong fix was reused in any run ([reuse-margin.md](reuse-margin.md)).

## How DejaFix uses Meko (judging: Meko use, 30%)

DejaFix calls 23 Meko tools; [meko-usage.md](meko-usage.md) lists each one with the file and the reason.

- **Conversations and traces:** one conversation per agent per ticket, and every step is a turn with
  reasoning, a plan and Claude's token usage (`conversation_create`, `conversation_add_message`,
  `track_token_usage`).
- **Memory and collective memory:**
  - approved fixes are stored by a curator agent and promoted to the datapack's collective memory (`memory_add`, `memory_promote`);
  - human corrections are memories of a human-review agent, found with `memory_search`.
- **Knowledge base:** the product docs and the librarian's articles (`knowledgebase_upload_document`,
  `knowledgebase_search`, `knowledgebase_document_status`); `context_search` runs before every solve.
- **Self-learning:** Meko's grades find weak searches; after an article is published, a replay
  measures the search again (`selflearning_status`, `_shortfalls`, `_query_log`, `_verify`). Two
  searches went from mid to high, and the new article ranked first.
- **Artifacts:** a case file for every human decision, with its hash checked (`artifact_put`).
- **Postgres (AMP):** the app's own database is a Meko-managed cluster, created by
  `manage.py provision_db` (`amp_core_budget`, `amp_cluster_*`, `amp_compute_list`).
- **Datapacks:** a fresh datapack per benchmark arm, so the arms can't see each other's memory (`datapack_create`).

## Impact (25%)

- About a third fewer tokens on repeat tickets, and about half the tickets need no person.
- Human judgment, not the model, decides what becomes shared memory. A correction changes what the
  next agent does, and the reviewer escalates a fix that ignores one.
- Safety held in every run: no fix was reused for another issue, both prompt-injection tickets were
  escalated with nothing stored, and lookalike tickets reused the fix for their real problem.

## Execution (20%)

- A recorded demo (`manage.py load_demo`) lets anyone browse the whole app without API keys.
- Django app with a full staff UI: pipeline strip, shared-memory page, review panel, light and dark
  themes, works without JavaScript, strict Content-Security-Policy.
- 260 tests, run by GitHub Actions on every push, against an in-process fake of Meko's MCP server.
- A final OWASP Top 10 review fixed five findings (two race conditions, an unhandled upload error,
  unvalidated values written to `.env`, a stale publish) and now requires a written reason to approve
  a fix the reviewer agent escalated.
- Every design decision and the source it was checked against is in [decision-log.md](decision-log.md).

## Innovation (15%)

- Trust is checked when memory is read: only the curator's fixes and the human-review agent's
  corrections count, so memories Meko extracts from conversations, or a poisoning attempt, can't
  steer the agents.
- A measured self-learning loop: Meko's own grades choose the articles to write, and its replay
  proves whether they helped.
- A benchmark with lookalike tickets that tests the main danger of shared memory: reusing the wrong fix.

## Links

- Repository: https://github.com/MhmoodHmmam/dejafix
- Demo video (under 5 minutes): VIDEO_URL
- Architecture: [architecture.md](architecture.md) · Meko usage: [meko-usage.md](meko-usage.md) ·
  Benchmark: [benchmark.md](benchmark.md)

## Before submitting

- [ ] Video recorded with narration, uploaded to YouTube (Unlisted), with English captions
      (`dejafix-demo.srt`), and `VIDEO_URL` replaced above.
- [ ] The repository is public and the README's tests badge is green.
- [ ] The text above is pasted into the HackerEarth form before 2027-01-17, 11:59 PM PT.
