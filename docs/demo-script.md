# Demo video script (under 5 minutes)

The story: a support team that never solves the same ticket twice, because its agents share
memory in Meko and learn from human decisions.

## Before recording

- Start the server: `.\.venv\Scripts\python.exe manage.py runserver --insecure`, then log in at
  http://127.0.0.1:8000/ as a staff user.
- Use tickets that haven't been run yet. In the local database these are already used: T001, T002,
  T003, T005, T006, T007, T008, T010, T013, T014, T015, T018, T019, T021, T023, T025, T030 and T033
  (resolved or reviewed), and T017 (escalated). T006's fix is retired. Fresh pairs for a live demo: **T009 → T022** (an
  expired payment link) and **T011 → T024** (a payout short by the currency fee).
- **Run the agents** returns at once. The page shows each agent step as it finishes (about 15 to
  40 seconds in all), then the result: worth showing for a few seconds before cutting the wait.
- On Meko Postgres, open the dashboard once before recording: the first load waits about
  4 seconds for Meko's context quality, then it is cached for 5 minutes.
- Cost: about $0.04 per new solve and $0.02 per reuse.
- Recorded on 2026-11-28 with background runs: T007 → T019 (reuse, with the real progress panel
  captured once a second and shown at 5× speed), and on 2026-11-27 T003 → T015 (correction).
  T019 used 2,811 tokens against 5,567 for T007 (−50%), and T015 read the correction on T003.
  On 2026-11-28 T006's fix was retired after a product update (`data/kb-updates/`), and T030
  was solved from the docs and the reason (5,909 tokens), then approved.

## Scenes

### 0:00–0:23 · The problem

**Show:** the title, then three tickets about the same payout delay.

> Support teams solve the same problem again and again. Every agent, human or AI, starts from
> zero. DejaFix is a team of Claude agents that share one memory in Meko, so a problem solved
> once is never solved from scratch again.

### 0:23–0:53 · How it works

**Show:** the first diagram in [architecture.md](architecture.md), or the dashboard's pipeline strip.

> A ticket goes to a triage agent, then a resolver. Before solving anything, the resolver
> searches Meko: approved fixes, human corrections, and the docs. A reviewer agent checks the
> fix, and a human approves or rejects it. Only human-approved fixes become shared memory.

### 0:53–1:45 · First ticket: solved from the docs

**Show:** `/tickets/T007/` → **Run the agents** → the "Agents working" panel filling in step by
step → the pipeline strip at the top, the proposed fix, then "Why: every step the agents took".

> Arabic customer names look broken in a CSV export. No approved fix exists yet, so the resolver
> solves it from the documentation. Every step is a Meko conversation turn, with the agent's
> reasoning, its token usage, and its Meko conversation ID. About five thousand tokens.

**Then:** **Approve and share with every agent**.

> I approve it. The curator agent stores the fix in Meko memory, promotes it to the datapack's
> collective memory, and saves a case file of the evidence as a Meko artifact.

### 1:45–2:27 · Same problem, new customer: reused

**Show:** `/tickets/T019/` → **Run the agents** → the progress panel (triage, retrieve, reuse
check) → the purple "Reused an approved fix from shared memory" card (it links back to T007), the
skipped Review and Human stages, the reuse check step.

> A different customer, different words, same problem. The resolver finds the approved fix in
> Meko with a high score. A cheap check confirms it applies, and the reply is adapted to this
> customer. Half the tokens, and no human needed, because a human already approved this fix.

### 2:27–3:17 · A human correction becomes a learning

**Show:** `/tickets/T003/` → **Run the agents** → in "Your decision", **Reject** with:
"Tell them test keys start with lp_test_ and live keys with lp_live_, and each one only works
with its own base URL."

> When the fix is incomplete, the reviewer rejects it with a note. That note is stored in Meko
> as a correction from the human-review agent.

**Then:** `/tickets/T015/` → **Run the agents** → the Retrieve step's decision (learnings: 1) and
the fix, which now names lp_test_, lp_live_ and the base URL.

> The next resolver reads that correction before solving. The same mistake isn't made twice.

If the first fix already names both prefixes, approve it instead and say that the docs were enough.

### 3:17–3:57 · The shared memory, and correcting it

**Show:** `/memory/`: the counters, then T006's fix (approved, reused on T018). Open **Retire this
fix**, type the reason, retire it. Then the "Retired fixes" section, T018's warning, and T030's
Retrieve step (`retired_fixes_skipped`, `learnings: 1`) and its fix.

> Every fix a human approved, every reuse and the tokens it saved. But products change. T006's
> fix was right when it was approved; after the December update it's outdated. So a person
> retires it, with a reason. No resolver reuses it again, the reason is stored in Meko as a
> correction, and T018, which already reused it, is flagged for a second look. The next ticket
> with the same problem, T030, skips the retired fix, reads the reason, and is solved with the
> new steps. Once approved, that's the new shared fix.

### 3:57–4:13 · Closing its own knowledge gaps

**Show:** `/gaps/` → a verified gap → its before and after meters.

> Meko grades every search. DejaFix reads those grades, finds the questions its docs answer
> badly, and a librarian agent drafts an article from approved fixes. After a human publishes
> it, Meko replays the search: here it went from mid to high, and the article ranks first.

### 4:13–4:43 · Does shared memory help? The benchmark

**Show:** the top table of [benchmark.md](benchmark.md) (all four runs), then `/benchmark/` and
its lookalike rows (T037–T042).

> Same agents, same 36 tickets, in separate Meko datapacks: two runs without shared memory and
> three with it. With Meko: 30, 28 and 30 correct instead of 25 and 26, a third fewer tokens on
> repeat tickets, and about 20 tickets for a human instead of 36. Then six lookalikes,
> worded like one problem but caused by another: all six right with Meko, and none reused the
> wrong fix. Both prompt-injection tickets were escalated without storing anything.

### 4:43–4:56 · Close

**Show:** [meko-usage.md](meko-usage.md) on GitHub, or a ticket's trace in the Meko portal.

> Conversations, traces, token usage, memories, collective memory, the knowledge base,
> self-learning, artifacts, and even the app's own Postgres database: DejaFix runs on Meko.
> Never solve it twice.

## Recording tips

- Windows: Xbox Game Bar (Win+Alt+R) records one window, and Clipchamp can cut the waits.
- Use a browser zoom of 110–125% so the text reads on a small screen. The pages follow the
  system's light or dark theme.
- Keep the narration in English: the judges are international.
