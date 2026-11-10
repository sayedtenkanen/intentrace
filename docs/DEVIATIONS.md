# Deviations from Slice 01 Brief

This file records divergences from `docs/slices/slice-01.md` as required by §8 of the
original brief. Silent divergence is the failure mode this project exists to catch; it
cannot be the norm inside the project itself.

---

## Standing rule: comments are part of behaviour changes

When you change what code does, the comment above it is part of the change. A comment that
overstates, understates, or contradicts the code it describes is a defect of the same kind
the product exists to detect. This has been found three times in review:

1. **Streaming that buffered** — `_iter_lines` said it streamed without buffering, then
   materialized every line into a list (D1).
2. **JSON encoding called length-prefixed** — `canonical_json` said it used
   length-prefixed fields; it JSON-encoded a list (B4, pre-slice-2).
3. **Unstripped offsets claimed as final** — `_split_sentences` said offsets were
   unstripped segment boundaries without noting the caller tightens them (R6, pre-slice-2
   remediation).

The rule: when you change behaviour, update the docstring, inline comment, and deviation
record (if any) in the same commit. Do not leave a comment that describes the old behaviour.

---

## D1 — Log reader materialises all lines

**Date:** 2026-09-14
**Brief section:** §5.1 Log

The brief required `read_observations` to return a generator and explicitly forbade
materializing the file as a list in the reader. The implementation reads all lines
into a list.

**What was done instead:** `read_observations` returns a `LogReadResult` containing a
list of observations and an optional `TornLineError`. The log is small in Slice 1 and
streaming was deferred.

**Why:** The log is small in v1. Streaming was deferred to a later slice when the log
may be larger.

---

## D2 — Extractor-side anchoring

**Date:** 2026-09-14
**Brief section:** §5.3 Extractor

The brief described the `FakeExtractor` as emitting no anchors; anchors were instead
re-derived at query time by a second heuristic in `cli.py::_resolve_symbols_from_statement`.

**What was found:** This created two copies of the same heuristic with diverging
skip-word lists, and meant `node_hash` was computed against current source every time —
making drift detection impossible by construction.

**What was done instead:** The `FakeExtractor` now accepts a `symbols` dict mapping
symbol names to `AnchorRef`s. The CLI builds this table by scanning all Python files
in the repo and passes it to the extractor. Anchors are attached at extraction time,
capturing `node_hash` against the source as it is then. The duplicate heuristic in
`cli.py` was deleted.

**Why:** One heuristic, one place. Drift detection requires a baseline hash captured
at extraction time, not a fresh hash computed at query time.

---

## D3 — Repo-relative symbol paths

**Date:** 2026-09-14
**Brief section:** §6.4 Anchor

The brief specified `symbol_path` as `<repo-relative-file>::<Class>::<func>`. The
initial implementation used `Path(file_path).name`, which dropped the directory
component. `src/pkg/retry.py::attempt` and `tests/fixtures/retry.py::attempt`
collided.

**What was found:** Two files with the same basename in different directories produced
identical symbol paths, breaking the anchor model.

**What was done instead:** `_symbol_path` now uses the full `file_path` argument
(which is repo-relative) rather than `Path(file_path).name`.

**Why:** Per the spec, symbol paths must be unique across the repo. Directory
components are required for disambiguation.

---

## D4 — Sentence splitter tears `Class.method` references apart

**Date:** 2026-09-18
**Brief section:** §5.3 Extractor (anchor target: `word`, `word.word`, `Class.method`)

`_split_sentences` splits on every `.`, so a `Class.method` reference never survives
to resolution intact: "The Alpha.attempt function must retry." becomes "The Alpha."
(skipped, no normative marker) and "attempt function must retry." (bare-name lookup).
The `CLASS_METHOD_RE` branch in `_resolve_symbols` is therefore unreachable through
`extract()` — qualified references silently degrade to bare-name handling.

**What was done instead:** `_resolve_symbols` now resolves `Class.method` through the
qualified path first (falling back to the bare name), which is correct at the function
level and pinned by white-box tests — but end to end the splitter still tears the
reference first.

**Why deferred:** repairing the splitter changes statements and provenance spans, which
changes `req_id`s. Under the identity design note (§3) that is an extractor behaviour
change and belongs with an `extractor_version` bump and its migration, not smuggled
inside a review fix. Owned by a future slice touching extraction.

---

## Slice 4 remediation — one log, no clock, and attestation history

**Date:** 2026-11-11

The slice 4 merge left a crash on a valid timezone-less observation, replayed
decisions by timestamp (so a backwards clock could undo an accepted drift), and
kept newer decision kinds in a companion file. This pass removed the companion,
made position in one append-only log the only order, and completed the
definition of done. What follows records the design choices that are not
obvious from the code.

### The `attest` decision kind

`Decision(kind="attest")` is one recorded human act that replaces the full
anchor baseline of a ratified requirement. It carries:

- `reason` — one of `late-anchor`, `rename`, `move`, `drift-accepted`. The tool
  assigns it from what it observes; the human cannot choose a cheaper reason.
  A changed body (or a vanished anchor with no replacement) is
  `drift-accepted`; a missing anchor replaced in the same file is `rename`, in
  another file `move`; an anchor with no baseline is `late-anchor`.
- `rationale` — required when `reason` is `drift-accepted`, rejected otherwise
  by the model. It is displayed by `why`.
- `baseline_hashes` and `baseline_name_hashes` — the complete replacement
  baseline, so a deletion is acknowledged by an empty baseline rather than by
  silence.

A malformed attestation (unknown reason, or drift with no rationale) is
rejected at the model boundary, so a bad log line is corruption rather than a
silent baseline replacement.

### The snapshot on ratify

`Decision(kind="ratify")` carries `RequirementSnapshot` (statement, provenance
spans, extractor version). The view builds active requirements from the latest
snapshot-bearing ratify, not from fresh extraction. A ratify written before
snapshots existed has none; it is reported orphaned with the reason and is
never silently promoted. `RequirementSnapshot` enforces the non-empty
provenance invariant, so a malformed snapshot cannot crash the view.

### The name-insensitive hash on baselines

Every baseline records `baseline_name_hashes[path]`, a hash of the normalized
subtree with the definition's own name erased from the `def` line, recursive
calls, and decorators that reference it (such as `@name.setter`). It exists so
a rename can be proposed without re-deriving the old body. It is not identity:
`req_id` still excludes anchors, and a rename never changes a requirement's
identity.

### Unknown decision kinds

`Decision.kind` is a plain string, not a closed set. A reader that meets a kind
it does not know keeps the record, reports it in `settle` as written by a newer
version, and continues loading the rest of the log. It is not corruption. A log
written from slice 4 onward is nevertheless not readable by a pre-slice-4
build, because that build used a closed kind set; no build was released, so no
rollback compatibility is owed.

### What happened to `detached`

`detached` was never a decision kind or a stored state. It is an internal stale
reason produced by `check_fresh`: the proposed anchor still resolves in the
symbol table but the current extraction no longer attaches it to this
requirement (for example, the name became ambiguous). Ratification is refused
as stale and the re-derived candidate is presented. A ratified baseline is not
silently shrunk when the extractor drops an anchor; it becomes `orphaned`
(`missing`) or `unconfirmed` (`changed`) against current code, and only an
`attest` replaces it.

### `why` history

The default `why` output shows the count of attestations appended after the
ratification, the last date, actor and reason, and the last `drift-accepted`
rationale. `why --history` prints every attestation and every rationale in
append order. The flag is the chosen way to keep earlier rationales reachable
without opening the log by hand.

### Open — same words, different code

A sketch whose words match a ratified snapshot but whose proposed anchor the
snapshot lacks is currently attached to the existing requirement as a late
anchor. The intended presentation is a separate "same words, different code"
item for a decision. The case cannot arise with `FakeExtractor`, because the
same words resolve to the same symbols; a model-backed linker will reach it.
This is recorded as **open** for the owner to decide before slice 5. This pass
did not change the behaviour.
