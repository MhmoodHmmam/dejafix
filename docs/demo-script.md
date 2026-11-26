# Demo video script (under 5 minutes)

The story: a support team that never solves the same ticket twice, because its agents share
memory in Meko and learn from human decisions.

## Before recording

- Start the server: `.\.venv\Scripts\python.exe manage.py runserver --insecure`, then log in at
  http://127.0.0.1:8000/ as a staff user.
- Use tickets that haven't been run yet. In the local database, T001, T002, T013, T014, T025 and
  T033 are closed, and T017 is escalated. The script uses **T006 → T018** (reuse) and
  **T010 → T023** (correction).
- Each **Run the agents** click takes 15 to 30 seconds. Cut the wait in editing.
- On Meko Postgres, open the dashboard once before recording: the first load waits about
  4 seconds for Meko's context quality, then it is cached for 5 minutes.
- Do one full practice run first. A practice run uses up its tickets, so then record with
  another pair, for example T008 → T021 and T003 → T015.
- Cost: about $0.04 per new solve and $0.02 per reuse.
- Rehearsed on 2026-11-26 with T002 → T014: approving T002 promoted its fix, and T014 reused it.
  T014 used 2,801 tokens in 8.1 s, against 5,779 tokens in 17.4 s for T002 (−52% tokens).

## Scenes

### 0:00–0:20 · The problem

**Show:** the dashboard (`/`).

> Support teams solve the same problem again and again. Every agent, human or AI, starts from
> zero. DejaFix is a team of Claude agents that share one memory in Meko, so a problem solved
> once is never solved from scratch again.

### 0:20–0:50 · How it works

**Show:** the first diagram in [architecture.md](architecture.md) on GitHub.

> A ticket goes to a triage agent, then a resolver. Before solving anything, the resolver
> searches Meko: approved fixes, human corrections, and the docs. A reviewer agent checks the
> fix, and a human approves or rejects it. Only human-approved fixes become shared memory.

### 0:50–1:50 · First ticket: solved from the docs

**Show:** `/tickets/T006/` → **Run the agents** (cut the wait) → scroll to "Why: every step
the agents took".

> This customer's invoices have no VAT. No approved fix exists yet, so the resolver solves it
> from the documentation. Every step is a Meko conversation turn, with the agent's reasoning,
> its token usage, and its Meko conversation ID. About five and a half thousand tokens.

**Then:** click **Approve and share with every agent**.

> I approve it. The curator agent stores the fix in Meko memory, promotes it to the datapack's
> collective memory, and saves a case file of the evidence as a Meko artifact.

### 1:50–2:35 · Same problem, new customer: reused

**Show:** `/tickets/T018/` → **Run the agents** → the "Reused an approved fix" badge, the reuse
check step and the token count.

> A different customer, different words, same problem. The resolver finds the approved fix in
> Meko with a high score. A cheap check confirms it applies, and the reply is adapted to this
> customer. Half the tokens, half the time, and no human needed, because a human already
> approved this fix.

### 2:35–3:20 · A human correction becomes a learning

**Show:** `/tickets/T010/` → **Run the agents** → **Reject with this note**:
"Tell them to honour the Retry-After header and to use /v2/batch for bulk imports."

> When the fix is incomplete, the reviewer rejects it with a note. That note is stored in Meko
> as a correction from the human-review agent.

**Then:** `/tickets/T023/` → **Run the agents** → open the retrieve step's Decision (learnings: 1)
and show the fix now mentions Retry-After and /v2/batch.

> The next resolver reads that correction before solving. The same mistake isn't made twice.

If T010's first fix already mentions both, approve it instead and say that the docs were enough.

### 3:20–3:55 · Closing its own knowledge gaps

**Show:** `/gaps/` → a published gap → its band before and after.

> Meko grades every search. DejaFix reads those grades, finds the questions its docs answer
> badly, and a librarian agent drafts an article from approved fixes. After a human publishes
> it, Meko replays the search: here it went from mid to high, and the article ranks first.

### 3:55–4:35 · Does shared memory help? The benchmark

**Show:** `/benchmark/`, then the top table of [benchmark.md](benchmark.md).

> Same agents, same 36 tickets, run twice in separate Meko datapacks. The only difference is
> shared memory. With Meko: 30 correct instead of 25, a third fewer tokens on repeat tickets,
> and 19 tickets for a human instead of 36. No wrong reuse, and both prompt-injection tickets
> were escalated without storing anything.

### 4:35–4:55 · Close

**Show:** [meko-usage.md](meko-usage.md) on GitHub, or a ticket's trace in the Meko portal.

> Conversations, traces, token usage, memories, collective memory, the knowledge base,
> self-learning, artifacts, and even the app's own Postgres database: DejaFix runs on Meko.
> Never solve it twice.

## Recording tips

- Windows: Xbox Game Bar (Win+Alt+R) records one window, and Clipchamp can cut the waits.
- Use a browser zoom of 110–125% so the text reads on a small screen.
- Keep the narration in English: the judges are international.
