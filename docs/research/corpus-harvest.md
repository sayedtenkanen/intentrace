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

### Pass 2 measurements

Effective n is stated per number. Labelling by the agent; agent estimates.

**Corpus table**

| Sessions dir | Files | Sessions | Prompts | Range |
|---|---|---|---|---|
| auto-harnessessing | 5 | 5 | 41 | Jul 5–9 |
| worktree great-vaughan | 1 | 1 | 1 | Jul 8 |
| worktree loving-gould | 1 | 1 | 1 | Jul 8 |
| pr-review-machine (pass 1) | 2 | 3 | 22 | Jul 9–Sep 12 |
| **Total** | **9** | **10** | **65** | **Jul 5–Sep 12** |

**A1 — recall on short prompts: 0/5.** 62 short prompts labelled in full (56
zero-candidate, no sampling needed). 5 carry durable intent (3 agent-handoff
capability wants, 1 workflow-separation rule, 1 commit-hygiene invariant);
all 5 produced zero candidates, so captured = 0, missed-silent = 5,
missed-while-emitting = 0. Recall 0.0 at n=5. Sensitivity: flipping every
borderline call the skeptical way leaves 2 durable, still 0 captured — the
zero survives its own borderlines. (6 short prompts with candidates were all
non-durable; their 8 candidates are misses-by-construction for recall.)

**A2 — recall inside pastes.**

| Paste (session:turn) | Segments | Durable | Emitted | Recall | Precision |
|---|---|---|---|---|---|
| 95019ea3:0 (23KB spec) | 623 | 42 | 36 | 0.357 | 0.417 |
| 9d14dd67:16 (work order) | 127 | 6 | 8 | 0.50 | 0.375 |
| b6ac91f1:0 (tutorial doc) | 72 | 0 | 0 | n/a | n/a |
| Pooled (n_pastes=3) | 822 | 48 | 44 | 0.375 | 0.409 |

Miss mechanisms, counted on the mega-paste: ~two-thirds of missed durable
segments lack any normative marker (standing rules phrased as bare
declarations); the rest are splitter casualties — durable sentences torn
across segments ("Nothing rates / compliance.", "Findings sorted stably /
(class, then subject) so output diffs…") or list items with no verb, neither
half stating a complete constraint.

**B — variance, per document and per project.** Precision where defined:
mega-paste 15/36, work-order paste 3/8, six short docs 1/8, tutorial doc
undefined (0 emitted). Anchoring per project (current / historical tables):
pr-review-machine 1/36 both; auto-harnessessing 0/13 current, 1/13
historical; all others 0. Duplication: 1 within-session copy, 0 spanning
sessions (n=52 candidates). No confidence interval is computed across
sentences of one document; the pooled A2 recall above is flagged with its
n_pastes=3 and covers documents, not a population.

**C — anchoring with time-correct checkouts.** 20 distinct revs checked out
as detached worktrees outside every repo, removed afterwards. Result: 2/52
anchored historical vs 1/52 current-checkout — agreement within noise.
Unmeasured, not low: 11 prompts (missing checkout), 5 prompts (checkout
history starts after the prompt). The tutorial paste is a true negative
(0 durable, 0 emitted). The hypothesis holds: spec prose states behaviour
without naming functions, so the word-matches-symbol heuristic finds almost
nothing in either table regime.

**D — burst size (max candidates per single observation, per project).**
pr-review-machine 36 [0,0,36]; auto-harnessessing 8 (34 zeros then
1,1,1,1,1,8); great-vaughan 3 [3]; rest 0. Bursts are single-observation
events: the ≤40 ceiling survives on current evidence with a margin one
paste wide.

**What the numbers decide.**

- Pass-1 precision (0.42) survives as a band, not a point: 0.417, 0.375 on
  pastes, 0.125 on shorts-with-candidates. It was a property of pasted
  specs, which is where all of it still comes from.
- Recall is unacceptable, on the grounds that matter: A1 0/5 (stark,
  borderline-robust), A2 0.357–0.50 on pastes. The marker rule misses
  bare-imperative intent entirely and a third to two-thirds of in-document
  durable sentences. This is the stronger, better-evidenced case for moving
  slice 5 earlier that pass 1 could not make.
- The anchoring result is real (2/52 historical): most real requirements are
  unanchorable by word-matching, so scope predicates and cross-cutting
  support carry the design's weight and slice-2 drift covers a minority of
  real intent. Reorder slice priorities on this basis, not on precision.
- The ≤40 ceiling holds numerically (max 36) but a burst is one observation,
  so design the queue around per-observation bursts rather than trusting 40.
- `N` stays a guess: 2 tracked symbols (1 zero-width censored window, 1 no
  usable history). To fix it, harvest sessions whose repos have covering
  history; this corpus cannot.

---

## Pass 3 — extraction recall: decompose, fix the splitter, re-measure

### Part A — decomposing the deficit (no code changed)

Every missed durable unit (30 A2 segments + 5 A1 prompts = 35) assigned one
cause by reconstructing its true sentence and checking today's marker rule:

- **marker-miss** (31): the true sentence lacks any normative marker. Includes
  all 5 A1 prompts (4 verified single-segment; the fifth torn only at an
  email address, markerless on reconstruction — counted marker-miss per the
  both-rule) and 26 A2 segments (standing rules phrased as bare declarations:
  determinism, no-I/O, stable ordering, no-rating-language, and the whole
  test-code vocabulary of `assert`, which the rule never matches).
- **segmentation-miss** (4, all in the mega-paste): true sentences that do
  contain a marker, torn so neither half carries it — a `must` split from its
  clause by a line break, a `do not` stranded in a truncated parenthetical,
  and two `must`s separated from their constraints the same way.

Per paste: mega 4/27, work-order 0/3, shorts 0/5. Pooled: 4/35 ≈ 0.11.

- `recall_marker` (recall with perfect segmentation, today's rule):
  (18 emitted-durable + 4 recovered) / 53 durable units ≈ **0.42**.
- `recall_segmentation` (share of the deficit attributable to cutting):
  4/35 ≈ **0.11**.

Reading: cutting carries about a ninth of the deficit; the marker rule
carries the rest. Slice 5 still moves — but a ninth is worth recovering
without a model, which is Part B. Method note: each classification
reconstructed the true sentence from neighboring segments; "both"
(mis-cut and still markerless when fixed) counted as marker-miss.

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
