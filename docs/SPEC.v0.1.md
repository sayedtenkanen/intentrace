# intentrace — SPEC v0.1

**Status:** draft, pre-implementation. Nothing here is built.
Decisions are marked `[DECIDED]` or `[OPEN]`. Open decisions are load-bearing and are
listed again in §15 so they cannot be quietly skipped.

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
- No ReqIF / ASPICE export. (Deferred, not rejected — see §15.)

## 4. Definitions

| Term | Meaning |
|---|---|
| **Utterance** | An immutable captured event: a human prompt, a tool result, an accepted diff, a commit. Never edited, never interpreted in place. |
| **Requirement** | A statement of intended behaviour, with provenance, maturity, anchors and evidence. A derived view over utterances and code — never primary. |
| **Anchor** | A link from a requirement to a code location. Only localized requirements have anchors. |
| **Evidence** | What supports "still satisfied": a test id, a fitness-function id, or a dated human attestation. |
| **Ratification** | The recorded human act of promoting a candidate into an obligation. Distinct from accepting a diff. |
| **Settle** | An explicit checkpoint where reconciliation runs and the human works the queues. |
| **Waiver** | A recorded, owned, expiring exception to a requirement. |

## 5. Core invariants

These are the honesty rules. Every later design choice must preserve them; a feature that
violates one is out of scope by definition.

- **I1 — Log is truth.** The utterance log is the only ground truth. Requirements are a
  materialized view and must be re-derivable from the log. A better extractor re-runs over
  the same log and the two requirement sets are diffed.
- **I2 — Ratification is a separate act.** Accepting a diff means "this looks fine."
  Ratifying means "this is now an obligation." If these ever collapse into one action, the
  tool becomes a machine for retroactively justifying agent output.
- **I3 — Drift is measured from ratification.** Never from current code. The baseline is the
  state at the moment a human took responsibility.
- **I4 — Dangling anchors are events.** A lost anchor is reported, never silently re-attached.
  Semantic re-anchoring may *propose*; only a human or an explicit rule accepts.
- **I5 — No green without evidence.** No requirement reaches `satisfied` without evidence.
  Absence of evidence is a reported status, not silence.
- **I6 — Refactor invariance defines well-formedness.** A well-formed requirement is
  invariant under refactoring and violated by behaviour change. This is the quality test for
  extraction in both directions, and it is mechanically checkable.
- **I7 — Sketches gate nothing.** Unratified material never blocks any workflow.

## 6. Object model

### 6.1 Utterance

```
utterance_id      content hash
session_id        source session
turn_index        ordering within session
kind              prompt | tool_result | diff | commit
text              raw content, verbatim
timestamp
source            adapter id (e.g. claude-code-hook, transcript-backfill)
```

Append-only. Never rewritten.

### 6.2 Requirement

```
req_id            content-addressed (see OPEN-1)
statement         natural language, behavioural, implementation-free
origin            declared | recovered | co-derived
maturity          sketch | active | locked
status            see §7.2
provenance[]      spans: {utterance_id, start, end} — may be empty for pure-code recovery
derivation        {extractor_version, model, prompt_version, timestamp}
anchors[]         AnchorRef — empty for cross-cutting requirements
evidence[]        EvidenceRef
ratification      {actor, timestamp, settle_id} | null
waiver            {actor, reason, expires_at} | null
supersedes[]      req_ids this replaced
```

**`origin` is a spectrum, not a flag.** Reverse extraction reads the diff *together with*
the utterances that produced it, so the common case is `co-derived`: recovered from code,
but still citing prompt language where it exists.

### 6.3 Requirement kinds

- **Localized** — anchors to one or more code symbols. Drift is anchor-relative.
- **Cross-cutting** — no anchor exists ("never log PII", "all handlers are idempotent").
  Its evidence *is* a named check. These have no anchor and are never `orphaned`; they are
  `unverified` until a check claims them.

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

- **R** — the last ratified requirement set
- **C** — current code
- **U** — utterances since the previous settle

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

Code changed since the last settle that no requirement explains is **`unclaimed`**. In pure
SDD this would be a violation. Here it is the normal entry point for code-first work: the
queue from which recovered requirements are proposed.

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

Movement up is cheap. Movement down requires an explicit, recorded demotion. This is what
lets messy exploratory work proceed without friction while making regression expensive.

## 9. Checkpointing

- `intentrace settle` — explicit, human-initiated, the only point where questions are asked.
- Commit hook — records automatically in **sketch-only** mode. Asks nothing.

Explore for two hours, settle once. The tool must never interrupt.

## 10. Extraction

Behind `ExtractorPort`. Two directions, one reconciler:

- **Forward** — utterances → candidate requirements. Mostly segmentation and paraphrase.
- **Reverse** — (diff + producing utterances) → candidate requirements. Behavioural
  summarization, and the harder direction.

Reverse extraction has two characteristic failures, both of which I6 catches:

1. **Restating implementation** — "the retry loop runs 3 times" is the code with fewer
   symbols. It changes whenever the code changes, so it can never detect meaningful drift.
2. **Over-generalization** — inventing intent that was never present.

Slice 1 uses a `FakeExtractor`: deterministic, hand-written, no model in the loop.

## 11. Decay `[DECIDED]`

A sketch archives when **either**:

- a later settle touches the same anchors (the code moved on without it), or
- N settles pass with it untouched.

Archived, never deleted — the log keeps everything and archived sketches remain queryable.
Rationale: sketch bankruptcy (a 400-item queue nobody faces) is a more likely death than
triage fatigue, and it cannot be retrofitted into the data model later.

## 12. CLI surface (sketch)

```
intentrace settle                    # reconcile, work the queues
intentrace check [--strict]          # CI gate; non-zero on gate conditions
intentrace queue [unclaimed|sketch]  # inspect without settling
intentrace why <path>[:<line>]       # what intent covers this code
intentrace show <req_id>             # statement, provenance, anchors, evidence, history
intentrace ratify <req_id>           # the deliberate act (I2)
intentrace waive <req_id> --until    # recorded, owned, expiring
```

`check --strict` fails on: untriaged candidates past decay threshold, orphaned actives,
expired waivers, failing evidence on locked requirements.

CLI-first and CI-gate capable is deliberate. Agent plugins are a capture adapter, not the
product — a requirement system that lives only inside an agent session can block nothing.

## 13. Storage and stack `[DECIDED]`

- JSONL append-only utterance log as the write format; SQLite as a rebuildable index.
- Written fresh for this project — no code shared with Keel or AutoHarness. Schema freedom
  is the reason; the cost is solving torn-line detection and rebuildable indexing again.
- Python, uv + hatchling, pydantic v2, ruff + mypy strict. `[OPEN-7: confirm]`
- tree-sitter for anchoring, multi-language from day one: python, typescript, c.

## 14. Success criteria

"Works" is defined before building, as falsifiable claims:

1. **Catches planted drift.** A seeded behaviour change against a ratified requirement is
   reported as `unconfirmed` or `unsatisfied`, not missed.
2. **Zero false drift under refactor.** Rename, extract-function and reorder diffs produce
   no new requirements and no lost anchors.
3. **Never green without evidence.** No path exists by which a requirement reports
   `satisfied` with an empty evidence list.
4. **Deterministic re-derivation.** Re-running extraction over an unchanged log reproduces
   the same requirement identities.
5. **Bounded triage cost.** A settle after a normal working session presents a queue a human
   will actually face — target to be fixed by measurement, not guessed.

## 15. Open decisions

- **OPEN-1 — Requirement identity.** Content-addressed ids break on re-derivation: anchors,
  evidence and waivers all hold references. Needs an alias/merge table, and legitimate
  requirement *splits* make it harder than it looks. Likely deserves its own design note.
- **OPEN-2 — Degenerate mode.** A user who never ratifies gets an LLM documentation
  generator with extra ceremony. Is that an acceptable on-ramp, or a failure to design
  friction against? Probably the modal first-month behaviour either way.
- **OPEN-3 — Team ratification.** Who ratifies when the prompt author is not the reviewer.
  Out of scope for v1, but the `actor` field must not assume single-user.
- **OPEN-4 — ReqIF / ASPICE export.** Early makes this pitchable sooner; late keeps the core
  clean. The prompt→requirement→ReqIF path is a real unfilled gap in automotive.
- **OPEN-5 — Second agent adapter.** v1 assumes Claude Code hooks only.
- **OPEN-6 — License, public or private repo.**
- **OPEN-7 — Stack confirmation.** §13 assumes the Keel stack.

## 16. Known risks

| Risk | Shape | Current mitigation |
|---|---|---|
| Intent laundering | Recovered requirements auto-ratify; agent output becomes "what I meant" | I2, I3 |
| Sketch bankruptcy | Queue grows past facing; user stops looking | §11 decay |
| Reverse-extraction slop | Requirements that restate implementation | I6, enforced as a filter |
| Triage fatigue | Same death as spec-writing | Sketches gate nothing; settle is batched and explicit |
| False green | Tool reports health it cannot justify | I5, and success criterion 3 |

## 17. Slice plan

Written separately once this spec survives adversarial review. Shape:

- **Phase A — Capture:** utterance log, transcript backfill, hook adapter, diff correlation.
- **Phase B — Trace:** requirement store, triage CLI on `FakeExtractor`, tree-sitter
  anchoring, real extractor behind `ExtractorPort`, evidence, waivers.
- **Phase C — Drift:** verdict lattice, `check` gate, refactor-invariance fixture, coverage.

The refactor-invariance fixture moves **into Phase A**. It is not a late verification step —
it is the well-formedness test for everything the extractor produces.
