# intentrace — SPEC v0.2

**Status:** draft, pre-implementation. Nothing here is built.
Decisions are marked `[DECIDED]` or `[OPEN]`. Open decisions are load-bearing and are
listed again in §16 so they cannot be quietly skipped.

**Changes from v0.1** are summarised in §19. v0.1 is preserved at `docs/SPEC.v0.1.md`.

---

## 1. Problem

Agentic coding produces four failures that compound:

- **Comprehension debt** — the author does not know how their own code works.
- **Review overload** — too much generated code, too little information about why it exists.
- **Intent loss** — recovering the original intent means reading the implementation.
- **Regressions from forgetting** — agents break working behaviour while adding new behaviour.

Spec-Driven Development addresses these by moving the source of truth from code to a
human-authored specification. It has one structural assumption: **intent precedes
implementation.** That assumption is false often enough to matter. People explore in code,
discover what they meant, and settle the requirement afterwards. Tools that demand the
requirement first are abandoned during exactly the phase where they would help most.

## 2. Thesis

> Intent and code are two representations of the same system. They evolve independently and
> in either order. intentrace keeps them reconciled, in both directions, and reports the
> state of that reconciliation without ever overclaiming.

This is **not** spec-driven development. It is intent reconciliation. The tool demands
nothing up front and blocks nothing during exploration; obligations accumulate only when a
human deliberately settles them.

## 3. Non-goals (v1)

- Does **not** generate or modify code.
- Does **not** ask anyone to author a specification document.
- Does **not** claim code is correct. It reports reconciliation state only.
- No multi-user ratification, no merge of requirement sets across teammates.
- No ReqIF / ASPICE export. (Deferred, not rejected — see §16.)

## 4. Definitions

| Term | Meaning |
|---|---|
| **Observation** | An immutable captured event: a human prompt, a tool result, an accepted diff, a commit. Never edited, never interpreted in place. |
| **Decision** | An immutable recorded human act: ratify, waive, demote, archive. Also lives in the log. |
| **Utterance** | An observation. (Retained as the term for anything the extractor reads.) |
| **Requirement** | A statement of intended behaviour, with provenance, maturity, anchors and evidence. |
| **Anchor** | A link from a requirement to a code location. Localized requirements only. |
| **Scope** | A predicate (paths, languages, node kinds) by which a cross-cutting requirement claims code it does not anchor to. |
| **Evidence** | What supports "still satisfied": a test id, a check id, or a dated human attestation. |
| **Ratification** | The recorded human act of promoting a candidate into an obligation. Distinct from accepting a diff. |
| **Settle** | An explicit checkpoint where reconciliation runs and the human works the queues. |
| **Waiver** | A recorded, owned, expiring exception to a requirement. |

## 5. Core invariants

These are the honesty rules. Every later design choice must preserve them; a feature that
violates one is out of scope by definition.

- **I1 — Observations are truth; ratified requirements are primary.**
  The log holds observations and decisions, both immutable. *Sketches* are a materialized
  view over observations and re-derive freely. *Ratified requirements are primary* — a
  re-run of a better extractor may **propose successors**, never silently replace them.
  Re-derivation produces a migration proposal, not a new truth.
  *(v0.1 said requirements were purely a view. That was incompatible with I2 — see §19.)*
- **I2 — Ratification is a separate act.** Accepting a diff means "this looks fine."
  Ratifying means "this is now an obligation." If these ever collapse into one action, the
  tool becomes a machine for retroactively justifying agent output.
- **I3 — Drift is measured from ratification, and ratification is fresh.**
  The baseline is the state at the moment a human took responsibility. A candidate is
  **re-validated against current code at the instant of ratification**; a candidate whose
  code moved since it was proposed cannot be ratified as-is.
- **I4 — Dangling anchors are events.** A lost anchor is reported, never silently
  re-attached. Semantic re-anchoring may *propose*; only a human or an explicit rule accepts.
- **I5 — No green without evidence.** No requirement reaches `satisfied` without evidence.
  Absence of evidence is a reported status, not silence.
- **I6 — Refactor invariance defines well-formedness, and is enforced online.**
  A well-formed requirement is invariant under refactoring and violated by behaviour change.
  Offline this is measured on the fixture (§15). Online it is enforced by **alpha-rename
  equivalence** (§10.3).
- **I7 — Sketches gate nothing.** Unratified material never blocks any workflow.
- **I8 — Green is earned.** The gate must distinguish *reconciled* from *unused*. A repo
  where nothing has been settled must not report the same result as one that reconciles.
- **I9 — Nothing is grounded in nothing.** Every requirement cites at least one observation
  span. Diffs are observations, so code-recovered requirements cite the diff they came from.

## 6. Object model

### 6.1 Log entries

Append-only. Never rewritten. Two kinds:

```
Observation {
  obs_id        content hash
  session_id    source session
  turn_index    ordering within session
  kind          prompt | tool_result | diff | commit
  text          raw content, verbatim
  timestamp
  source        adapter id (claude-code-hook, transcript-backfill)
  causes[]      obs_ids this entry is attributed to (see §9.3)
}

Decision {
  dec_id        content hash
  kind          ratify | waive | demote | archive | migrate
  req_id        target
  actor
  timestamp
  settle_id
  rationale     optional free text
}
```

### 6.2 Requirement

```
req_id            content-addressed (see OPEN-1)
statement         natural language, behavioural, implementation-free
origin            declared | recovered | co-derived
maturity          sketch | active | locked
status            see §7.2
provenance[]      spans: {obs_id, start, end} — MUST be non-empty (I9)
derivation        {extractor_version, model, prompt_version, timestamp}
anchors[]         AnchorRef — empty for cross-cutting requirements
scope             ScopePredicate | null — required iff anchors is empty
evidence[]        EvidenceRef
ratification      {actor, timestamp, settle_id, baseline_hashes} | null
waiver            {actor, reason, expires_at} | null
supersedes[]      req_ids this replaced
```

**`origin` is a spectrum, not a flag.** Reverse extraction reads the diff *together with*
the observations that produced it, so the common case is `co-derived`: recovered from code,
but still citing prompt language where it exists. Where no prompt exists, provenance cites
the diff observation — never empty (I9).

### 6.3 Requirement kinds

- **Localized** — anchors to one or more code symbols. Drift is anchor-relative.
- **Cross-cutting** — no anchor exists ("never log PII", "all handlers are idempotent").
  Its evidence *is* a named check. It carries a **scope predicate** instead of anchors:

```
ScopePredicate {
  paths[]       glob patterns
  languages[]   optional
  node_kinds[]  optional (e.g. function_definition)
  exclude[]     glob patterns
}
```

Scope is what lets a cross-cutting requirement claim code without anchoring to it. Without
it, code governed only by cross-cutting intent is permanently `unclaimed` and the queue
churns forever (§7.3).

### 6.4 Anchor

```
AnchorRef {
  lang         python | typescript | c       # v1 set
  file         repo-relative path
  symbol_path  e.g. retry.py::RetryPolicy::attempt
  node_kind    tree-sitter node type
  node_hash    hash of the normalized subtree
  marker       optional: `req:<id>` comment found in source
}
```

Marker comments win when present. Otherwise symbol path identifies, node hash detects change.

## 7. Reconciliation

### 7.1 The three-way read

At each settle, intentrace reads:

- **R** — the current ratified requirement set
- **C** — current code
- **O** — observations since the previous settle

and produces a verdict per requirement plus a set of code changes no requirement explains.

### 7.2 Verdict lattice

| Verdict | Trigger |
|---|---|
| `satisfied` | anchors intact since ratification, evidence passing |
| `unverified` | active, zero evidence — the state every requirement is born into |
| `unconfirmed` | anchored node hash changed since last attestation |
| `orphaned` | anchor symbol deleted or moved beyond recognition |
| `unsatisfied` | claimed test or check fails, or was deleted |
| `unimplemented` | ratified, no anchor was ever established |
| `conflict` | contradicts another active requirement |
| `waived` | explicit, unexpired waiver |

`unconfirmed` is the silent-drift case and is the reason the tool exists.

### 7.3 The unclaimed queue

Code changed since the last settle that no requirement explains is **`unclaimed`**:

> unclaimed ⟺ not covered by any active anchor **and** not matched by any active scope predicate

In pure SDD this would be a violation. Here it is the normal entry point for code-first
work: the queue from which recovered requirements are proposed.

The two unmatched queues — requirements with no code, code with no requirement — are the
product. The UX is working them down from either end, in any order.

## 8. Maturity ratchet

```
sketch  ──ratify──▶  active  ──attach evidence──▶  locked
   ▲                    │                              │
   └──── archive ───────┘◀────── explicit demotion ────┘
```

- `sketch` — proposed, unratified. Gates nothing. Free to accumulate during exploration.
- `active` — ratified. Drift is reported.
- `locked` — has evidence. Drift is a hard gate failure.

Movement up is cheap; movement down is an immutable `demote` decision with an actor and a
rationale. **Demotion is rate-limited by the gate (§12.2)** — an unbounded demotion path is
a legal move to green.

Ratification re-validates freshness (I3): if the anchored code changed between proposal and
ratification, the candidate is re-derived and re-presented rather than accepted.

## 9. Checkpointing and attribution

### 9.1 Checkpoints

- `intentrace settle` — explicit, human-initiated, the only point where questions are asked.
- Commit hook — records automatically in **sketch-only** mode. Asks nothing.

Explore for two hours, settle once. The tool must never interrupt.

### 9.2 Sessions

Observations carry `session_id`. Multiple sessions may write to one repo concurrently —
two terminals, or a subagent. The log is append-only and tolerates interleaving.

### 9.3 Diff attribution `[OPEN-8]`

Correlating a diff to the prompts that produced it is ambiguous under interleaving, and
mis-attribution is a **correctness bug in a tool whose value is provenance** — not an edge
case. v1 approach: attribute by `session_id` plus the observation window within that
session, recorded in `causes[]`; where sessions overlap on the same files, mark attribution
`ambiguous` and surface it at settle rather than guessing. A stronger model is open.

## 10. Extraction

### 10.1 Directions

Behind `ExtractorPort`. Two directions, one reconciler:

- **Forward** — observations → candidate requirements. Mostly segmentation and paraphrase.
- **Reverse** — (diff + producing observations) → candidate requirements. Behavioural
  summarization, and the harder direction.

### 10.2 The two reverse-extraction failures

1. **Restating implementation** — "the retry loop runs 3 times" is the code with fewer
   symbols. It changes whenever the code changes, so it can never detect meaningful drift.
2. **Over-generalization** — inventing intent that was never present.

### 10.3 Online well-formedness: alpha-rename equivalence `[DECIDED]`

I6 cannot be enforced online by classifying diffs as refactors — that classification is the
problem the tool exists to solve. Instead:

> Mechanically alpha-rename the identifiers in the diff. Extract a candidate from both the
> original and the renamed diff. **If the two candidates differ, reject the candidate.**

Alpha-renaming is semantics-preserving by construction, so any difference in output is the
extractor leaking implementation into the statement. This catches failure mode 1 directly
and runs on every extraction. Failure mode 2 remains a human-triage problem.

Offline, the fixture (§15) measures the full I6 property against scripted refactors.

### 10.4 Determinism

Model output is not reproducible, at any temperature, across versions. Identity stability
therefore comes from **caching keyed by `(provenance span, extractor_version)`**, not from
model determinism. A changed extractor version produces an explicit migration proposal
(I1), never a silent re-identification.

Slice 1 uses a `FakeExtractor`: deterministic, hand-written, no model in the loop.

## 11. Decay `[DECIDED]`

A sketch archives when **either**:

- a later settle touches the same anchors (the code moved on without it), or
- N settles pass with it untouched.

Archived, never deleted — the log keeps everything and archived sketches remain queryable.
Rationale: sketch bankruptcy (a 400-item queue nobody faces) is a more likely death than
triage fatigue, and it cannot be retrofitted into the data model later.

## 12. CLI surface and gate

### 12.1 Commands (sketch)

```
intentrace settle                    # reconcile, work the queues
intentrace check [--strict]          # CI gate; non-zero on gate conditions
intentrace queue [unclaimed|sketch]  # inspect without settling
intentrace why <path>[:<line>]       # what intent covers this code
intentrace show <req_id>             # statement, provenance, anchors, evidence, history
intentrace ratify <req_id>           # the deliberate act (I2)
intentrace waive <req_id> --until    # recorded, owned, expiring
```

### 12.2 Gate conditions

Fails on:

- orphaned active requirements
- expired waivers
- failing evidence on `locked` requirements
- sketches past the decay threshold, untriaged

**And, per I8, on vacuity:**

- **coverage floor** — unclaimed-to-claimed ratio above a configured threshold
- **staleness floor** — no settle within N commits
- **demotion ceiling** — demotions per window above a configured rate

Without the vacuity conditions, a repo that has never been settled passes `--strict`, and
"green" means *nothing is being checked*. That is the same false-green failure as I5, one
level up, where I5 cannot see it.

CLI-first and CI-gate capable is deliberate. Agent plugins are a capture adapter, not the
product — a requirement system that lives only inside an agent session can block nothing.

## 13. Storage and stack `[DECIDED]`

- JSONL append-only log (observations + decisions) as the write format; SQLite as a
  rebuildable index.
- Written fresh for this project — no code shared with Keel or AutoHarness. Schema freedom
  is the reason; the cost is solving torn-line detection and rebuildable indexing again.
- Python, uv + hatchling, pydantic v2, ruff + mypy strict. `[OPEN-7: confirm]`
- tree-sitter for anchoring, multi-language from day one: python, typescript, c.

**C is parse-level only in v1.** tree-sitter parses C but macro semantics are invisible to
it, and extract-function refactors in C frequently move macros. Success criterion 2 will be
materially weaker for C than for Python. This matters because C is the language any future
ASPICE path depends on — stated here so it is not discovered later.

## 14. Success criteria

"Works" is defined before building, as falsifiable claims:

1. **Catches planted drift.** A seeded behaviour change against a ratified requirement is
   reported as `unconfirmed` or `unsatisfied`, not missed.
2. **Zero false drift under refactor.** Rename, extract-function and reorder diffs produce
   no new requirements and no lost anchors. (Python target; see §13 for C.)
3. **Never green without evidence.** No path exists by which a requirement reports
   `satisfied` with an empty evidence list — and no path exists by which a repo with no
   settled intent reports a passing `--strict` gate (I8).
4. **Stable identity under re-run.** Re-running extraction over an unchanged log reproduces
   the same requirement identities **by cache** (§10.4). A changed extractor version yields
   an explicit migration proposal, never silent re-identification.
5. **Bounded triage cost.** A settle after a normal working session presents a queue a human
   will actually face — target to be fixed by measurement, not guessed.

## 15. Refactor-invariance fixture

A small repo plus scripted, semantics-preserving diffs: rename symbol, extract function,
inline variable, reorder independent statements, move function between modules. Plus a
matched set of behaviour-changing diffs (change a constant, invert a condition, drop a
branch).

Expected: zero new requirements and zero lost anchors on the first set; detection on the
second. This is built in **Phase A**, not at the end — it is the only mechanical test that
separates a real requirement from a restatement of the code, so the extractor cannot be
judged without it.

## 16. Open decisions

- **OPEN-1 — Requirement identity and migration.** Partially resolved by I1: ratified
  requirements are primary, so re-derivation cannot orphan decision history. Still open:
  the migration proposal format, and how legitimate requirement *splits* are represented.
  Deserves its own design note.
- **OPEN-2 — Degenerate mode.** A user who never ratifies gets an LLM documentation
  generator with extra ceremony. Acceptable on-ramp, or a failure to design friction
  against? Probably the modal first-month behaviour either way.
- **OPEN-3 — Team ratification.** Who ratifies when the prompt author is not the reviewer.
  Out of scope for v1, but `actor` must not assume single-user.
- **OPEN-4 — ReqIF / ASPICE export.** Early makes this pitchable sooner; late keeps the core
  clean. The prompt→requirement→ReqIF path is a real unfilled gap in automotive.
- **OPEN-5 — Second agent adapter.** v1 assumes Claude Code hooks only.
- **OPEN-6 — License, public or private repo.**
- **OPEN-7 — Stack confirmation.** §13 assumes the Keel stack.
- **OPEN-8 — Diff attribution under concurrent sessions.** §9.3.

## 17. Known risks

| Risk | Shape | Current mitigation |
|---|---|---|
| Intent laundering | Recovered requirements auto-ratify; agent output becomes "what I meant" | I2, I3 |
| Vacuous green | Gate passes because nothing is checked | I8, §12.2 |
| Lost decision history | Re-derivation orphans ratifications; gate silently greens | I1 |
| Sketch bankruptcy | Queue grows past facing; user stops looking | §11 decay |
| Reverse-extraction slop | Requirements that restate implementation | I6, §10.3 |
| Stale ratification | Ratifying a statement about code that already moved | I3 freshness |
| Queue churn | Cross-cutting-only code permanently unclaimed | §6.3 scope |
| Triage fatigue | Same death as spec-writing | I7; settle is batched and explicit |
| Mis-attribution | Diff credited to the wrong prompt | §9.3, OPEN-8 |

## 18. Slice plan

**Vertical, not layered.** v0.1 described horizontal phases in which nothing was usable
until mid-Phase B. That contradicts the method used on Keel and AutoHarness.

- **Slice 1 — `intentrace why`, end to end, on `FakeExtractor`.**
  Log write → deterministic candidate → store → anchor (Python only) → `why <file>` prints
  the intent covering that code. Thin through every layer, no model, no gate.
- **Slice 2 — settle loop.** Ratification as a recorded decision, freshness re-validation,
  the sketch queue.
- **Slice 3 — unclaimed queue and reverse extraction** on `FakeExtractor`.
- **Slice 4 — refactor-invariance fixture** and alpha-rename equivalence.
- **Slice 5 — real extractor** behind `ExtractorPort`, with the §10.4 cache.
- **Slice 6 — verdict lattice and `check`**, including the §12.2 vacuity conditions.
- **Slice 7+ — tree-sitter TS and C, evidence links, waivers, decay, coverage report.**

Full slice plan written separately once this spec survives review.

## 19. Changes from v0.1

| # | Change | Why |
|---|---|---|
| 1 | I8 added; §12.2 vacuity gate conditions | v0.1's gate passed on a repo where nothing had ever been settled — green meant "nothing is checked" |
| 2 | I1 rewritten: ratified requirements are primary | v0.1's "requirements are a pure view" contradicted I2, and re-derivation could orphan every ratification, silently greening the gate |
| 3 | §10.3 alpha-rename equivalence | v0.1 claimed I6 was "enforced as a filter"; it is only measurable offline. Alpha-rename equivalence is an online test that works |
| 4 | `ScopePredicate` on cross-cutting requirements | Without it, code governed only by cross-cutting intent is permanently unclaimed and the queue churns every settle |
| 5 | I3 extended with ratification freshness | A sketch could be ratified after its code moved, then immediately report `satisfied` against a stale baseline |
| 6 | Criterion 4 restated around caching | Model output is not reproducible; determinism by cache, migration on version change |
| 7 | §9.2–9.3 sessions and attribution; OPEN-8 | Concurrent sessions make diff→prompt correlation ambiguous; mis-attribution is a correctness bug here |
| 8 | I9; provenance must be non-empty | v0.1 allowed empty provenance for code recovery, falsifying the grounding claim for that whole class |
| 9 | §18 slice plan made vertical | Horizontal phases delayed all user-visible value to mid-Phase B |
| 10 | §13 C preprocessor caveat | Refactor invariance is materially weaker for C, the language the ASPICE path needs |
