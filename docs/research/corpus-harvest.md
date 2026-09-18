# Corpus harvest — FakeExtractor against real transcripts

**Status:** experiment, not a slice. Deliverable is evidence. No file under `src/`
was modified; the extractor code is untouched. Pipeline: `tools/harvest.py`
(seed `20260918`). This document contains aggregate statistics only — no prompt
text, no transcript content. Labelling was done by the implementing agent, so the
precision figure is an **agent estimate, not ground truth**.

Scope approvals: `pr-review-machine` sessions approved first; all turns within
them approved second (those sessions also worked in other checkouts). Two scope
questions were asked and answered before any file content was read.

---

## Pass 1 (intact — see below; unedited since publication)

---

## Pass 2 — recall, and a second corpus

Pass 2 corpus approved by a third scope question (auto-harnessessing
histories), answered before any new file content was read. All label-derived
pass-2 figures are agent estimates, not ground truth.

### Pass 2 corpus (file inventory only; no file opened yet)

| Sessions dir | Files | Sizes (bytes) | mtimes |
|---|---|---|---|
| auto-harnessessing | 5 session files | 201K–1.3M | Jul 9 – Sep 19 |
| …--great-vaughan (worktree) | 1 session file | — | — |
| …--loving-gould (worktree) | 1 session file | — | — |
| pr-review-machine (pass 1) | 2 session files | — | Jul–Sep |

(Session / prompt / range table follows after parsing; counts are metadata,
gathered after the rubric commit.)

### Pass 2 recall rubric (written before viewing any new prompt)

Recall asks "does this text contain durable intent" — a different judgement
about different objects than precision's "is this candidate durable".

- **contains-durable-intent** — The text states at least one constraint on
  system behavior that should hold beyond the current task and would be worth
  re-checking later. (Invented positive: "Retries must back off
  exponentially, never hammer the server." / "The cache must never serve
  stale entries after invalidation.")
- **no-durable-intent** — The text carries no such constraint: acknowledgements,
  questions with no stance, bare imperatives to do something now ("fix the
  typo", "go ahead" — invented negatives), pasted non-normative material, a
  fragment stating no complete constraint (same fragment rule as pass 1).

Units and pre-registered rules:

- **A1 (short prompts):** the whole prompt. Label durable-or-not; for
  durable-carrying prompts that produced candidates, judge captured-or-missed
  (does any candidate state the durable constraint?). Recall_A1 =
  captured / durable-carrying. Report components (n short, n durable-carrying,
  n captured, n missed-silent, n missed-while-emitting). Limitation disclosed:
  per-prompt, not per-requirement — an upper bound on true recall.
- **A2 (pastes):** each `_split_sentences` segment (the same segments
  extraction consumed; limitation recorded — splitter artifacts affect both
  sides equally). Recall_A2 = durable segments with ≥1 candidate /
  durable segments. Precision per paste falls out of the same labels.
- **Paste threshold (mechanical):** prompt ≥2000 chars OR ≥5 candidates.
  Short = complement.
- **Seeds:** extraction sampling `20260918` (unchanged); A1 sampling (iff
  >150 zero-candidate prompts) `20260919`.

### Pass 2 measurements (to follow)

---

## 1. Method and corpus

- **Projects:** 1 approved session directory (`pr-review-machine`).
- **Sessions:** 2 files, 3 distinct `sessionId` values (one file holds two sessions).
- **Date range** (prompt timestamps): 2026-07-09 → 2026-09-12.
- **Record shape** (verified, not assumed): one JSON object per line; `type`
  discriminates. Relevant: `user` with `message.content` as string (human prompt)
  or list of `tool_result` blocks (harness feedback, excluded); `assistant` with
  `thinking` / `tool_use` / `text` blocks; plus harness records
  (`queue-operation`, `attachment`, `last-prompt`, `ai-title`, `system`, `mode`,
  `file-history-*`, `bridge-session`, `custom-title`). Mapping rule: `user` +
  string content + `origin.kind == "human"` → `Observation(kind="prompt")`
  (real model, real extractor, per-prompt extraction so ambiguity attribution
  is exact). 3 string-content user records with non-human origin were counted
  and excluded. 292 `tool_result` user records excluded.
- **Symbol tables:** built from the record `cwd` checkout where it exists
  (`pr-review-machine`, `aspice-trace-agent`); `langgraph-orchestrator` no
  longer exists locally, so its 11 prompts ran without a table and could not
  anchor by construction.
- **Audit:** 22 prompts mapped; 0 unparseable lines.

## 2. Metrics

### Volume

| Measure | Value |
|---|---|
| Sessions with ≥1 prompt | 3 |
| Prompts | 22 (per session: 1, 10, 11; median 10, p90 11) |
| Prompt lengths (chars) | 21 prompts at 7–194 (median ~73); 1 prompt at 23,274 |
| Candidates | 36 (per session: 0, 0, 36; per prompt median 0, p90 0, max 36, mean 1.64) |
| Prompts producing ≥1 candidate | 1 of 22 (0.045) |

All 36 candidates come from the single 23KB prompt. 21 conversational steering
prompts produced nothing: the normative-marker rule is blind to normal
agent-directed prose, which carries no must/should/never/always.

### Anchoring

| Measure | Value |
|---|---|
| Anchored candidates | 1 of 36 (0.028) |
| Unanchored | 35 of 36 |
| Prompts with any ambiguity | 0 of 22 |
| Anchors on the anchored candidate | 1 |
| Prompts run without a symbol table | 11 of 22 |

The mega-prompt specifies a tool whose code lives in none of the tabled
checkouts, so its 35 unanchored candidates are unanchorable by construction,
not by extractor failure.

### Duplication

36 distinct normalized statements; 0 within-session copies; 0 statements
spanning sessions. Per-utterance identity produced no flooding here — but
nothing was restated either, so this corpus gives no evidence for or against
queue-level de-duplication.

### Decay input

1 anchored symbol tracked; status `no_history_no_commits` — its file exists
at HEAD but has no commits after the session date, and the checkout HEAD
predates the session. Zero usable survival observations. There is no empirical
basis for `N` in this corpus.

### Candidate shape (mechanical, aggregate)

Of the 36 presented statements: 11 begin with a lowercase letter (sentence
fragments, not sentences); 2 have unbalanced parentheses (truncated
mid-construct). The splitter tears bulleted/punctuated prose into pieces and
each piece becomes a "requirement" — e.g. a CLI usage fragment matched only
because `--fail-on never` contains the marker `never`.

## 3. Rubric (written before viewing any candidate)

- **durable requirement** — A constraint on system behavior that should still
  hold after the current task lands and would be worth re-checking later.
  (Invented examples: "Payments must be idempotent across retries." /
  "The API must never return another tenant's data.")
- **transient steering** — An instruction scoped to the immediate edit that
  expires once the change lands. (Invented: "Use the existing retry helper
  for this call." / "Keep the new test in the same file for now.")
- **junk** — Text with no actionable intent as presented: acknowledgements,
  thinking-out-loud fragments, pasted noise, truncated pieces stating no
  complete constraint. (Invented: "Thanks, that looks right." /
  "hm, wait — let me reconsider.")

Applied rule for fragments: a fragment stating a complete checkable claim
counts as durable; a truncated fragment stating none counts as junk; a direct
instruction to the agent with no durable claim counts as transient.
Borderline calls: idx 1, 18, 34 (fragment with complete claim → durable);
idx 7 (instruction without durable claim → transient); idx 28 (scenario
without its obligation verb → junk).

## 4. Sample results

- Seed `20260918`; population 36 < 100, so the sample is all candidates.
- Labels: durable 15, transient 1, junk 20.
- **Precision (durable share): 15/36 ≈ 0.42**, Wilson 95% interval
  [0.27, 0.58] (n=36). Agent estimate.
- Caveat that dominates everything: all 36 labels come from one pasted
  document, so they are heavily correlated — the effective sample of
  *documents* is 1. Treat 0.42 as a description of this paste, not of the
  extractor on prompts in general.

| idx | req | label |
|---|---|---|
| 0 | R-cab97397 | junk |
| 1 | R-a308f835 | durable |
| 2 | R-ac4c74c8 | durable |
| 3 | R-f3c3926a | junk |
| 4 | R-416f6dc0 | junk |
| 5 | R-8c87194e | durable |
| 6 | R-50f0ccfc | durable |
| 7 | R-94c036f8 | transient |
| 8 | R-b7f9e8ec | junk |
| 9 | R-ac5bc045 | junk |
| 10 | R-2796aed9 | durable |
| 11 | R-f515aaa3 | junk |
| 12 | R-1b29c829 | junk |
| 13 | R-01808b50 | durable |
| 14 | R-6ab932c7 | durable |
| 15 | R-73867a6b | junk |
| 16 | R-7645ce12 | junk |
| 17 | R-86ccde48 | junk |
| 18 | R-46bc2979 | durable |
| 19 | R-e7dd608f | junk |
| 20 | R-88708415 | durable |
| 21 | R-9ca5f553 | junk |
| 22 | R-2ef2afde | durable |
| 23 | R-ae36ac96 | durable |
| 24 | R-8f0d2594 | junk |
| 25 | R-fe410cc4 | junk |
| 26 | R-8ac27c8f | junk |
| 27 | R-0459bea1 | junk |
| 28 | R-9b48216e | junk |
| 29 | R-7143cadb | junk |
| 30 | R-e8319ecb | junk |
| 31 | R-f3ddec28 | durable |
| 32 | R-11bbbe7c | durable |
| 33 | R-fd3cc87f | junk |
| 34 | R-2705ca06 | durable |
| 35 | R-f572543b | durable |

(`req` is a content-hash prefix, not content; disputes welcome.)

## 5. Recommendations

- **Decay `N` (§11): no basis — stays a guess.** n=1, degenerate. To measure
  it, harvest sessions whose repos have covering history plus anchored
  candidates, or instrument current repos going forward. Do not fix `N` from
  this harvest.
- **Settle cadence: per session is viable, provisionally.** Observed settles
  face 0, 0, and 36 candidates. Cadence matters less than prompt type: floods
  arrive as pastes, steering arrives silent. Revisit after a wider harvest.
- **Queue-level de-duplication: not justified by this corpus.** Zero
  duplicates observed, zero restatements — no signal in either direction.
- **Criterion 5 target (provisional, n=3 sessions): a settle after a normal
  session (≤12 prompts) presents ≤40 candidates.** Observed max is 36.
  Do not slice on this number until a wider corpus tightens it; the target
  form (a per-settle ceiling) is the part worth keeping.
- **The normative-marker rule does not survive to v1 alone.** Prompt hit rate
  1/22; 21 steering prompts invisible; the single hit yields fragments at
  0.42 precision. Keep it as deterministic scaffolding, but the real
  extractor (slice 5) must arrive before v1 — this number says sooner.
- **What the numbers suggest (extractor untouched):** sentence splitting on
  every period manufactures fragment-candidates out of structured prose;
  `never` misfires inside CLI flags that contain it as a substring; spec-about-absent-code
  is unanchorable by construction; queue volume is paste-driven (median 0,
  max 36 per prompt), so triage UX must be designed for bursts, not averages.
