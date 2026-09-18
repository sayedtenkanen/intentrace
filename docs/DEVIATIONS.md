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
