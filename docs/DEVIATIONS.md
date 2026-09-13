# Deviations from Slice 01 Brief

This file records divergences from `docs/slices/slice-01.md` as required by §8 of the
original brief. Silent divergence is the failure mode this project exists to catch; it
cannot be the norm inside the project itself.

---

## D1 — Generator-reader requirement relaxed

**Date:** 2026-09-14
**Brief section:** §5.1 Log

The brief required `read_observations` to return a generator and explicitly forbade
materializing the file as a list in the reader. The implementation uses `readlines()`
and returns a list.

**What was found:** Torn-line detection requires reading the final line to determine
whether it is complete. A generator cannot inspect the last element without consuming
the entire stream first. The two goals — lazy reading and torn-line detection — are
in tension for small logs.

**What was done instead:** `read_observations` returns a `LogReadResult` containing a
list of observations and an optional `TornLineError`. The log is small in Slice 1;
the `Store` interface abstracts the storage layer so a streaming implementation can
slot in later without touching callers.

**Why:** The torn-line property is more valuable than streaming for v1. The store
interface preserves the option to add streaming later.

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
