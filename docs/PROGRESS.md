# Progress

One entry per slice. What was built, what was deliberately left out, and what is known to be
incomplete. Slice briefs themselves are local working documents and are not published; this
file is the public record of what each slice actually delivered.

---

## Slice 4 — durable ratification, and an exit for every open verdict

**Status:** complete; review pending.

**Delivered**

- **Ratification is primary.** A `ratify` decision carries a `RequirementSnapshot`
  (statement, provenance spans, extractor version). `apply_decisions` builds active
  requirements from snapshots, not from fresh extraction, so a version bump can no longer
  hide the ratified text. Decisions written before this slice have no snapshot; they are
  reported orphaned with the reason, never promoted.
- **Suppression.** A sketch is not proposed when a snapshot covers the same span in the
  same observation or the same normalised statement in any observation. Counts and reasons
  are shown in `settle`. A sketch proposing an anchor the snapshot lacks is attached as a
  late anchor; a partial span overlap is shown as a possible successor and stays in the
  queue.
- **Attest.** `intentrace attest <req_id>` records one act that sets the full baseline.
  The tool assigns the reason: `drift-accepted` (rationale required), `rename` or `move`
  when a missing anchor is replaced, otherwise `late-anchor`. `why` shows the attestation
  date, actor and reason.
- **Rename proposals.** Anchors record a name-insensitive hash; `settle` proposes the
  renamed symbol when exactly one same-file symbol matches. The hash erases the definition
  name in the `def` line, recursive calls, `self.method` references and decorators.
- **Every open verdict has an exit.** `settle` presents `unconfirmed` and `orphaned`
  requirements as attest items, one at a time, before sketches. No batch attest.
- **Unknown decision kinds** are read as written by a newer version, not corruption.
- 95 tests (9 new in `tests/test_slice4.py`, including the three exit tests); ruff, ruff
  format and `mypy --strict` clean.

**Intentional behaviour changes**

- A version bump no longer orphans a ratification written by this slice. The old
  `test_version_bump_orphans_old_ratifications` is replaced by
  `test_preslice4_ratification_is_reported_not_promoted`; the new behaviour is exit test 2
  in `test_slice4.py`.
- A ratified qualified anchor is pinned. Bare-name ambiguity introduced elsewhere no
  longer turns a clean requirement `unconfirmed`; it cannot change what was ratified.
  `test_why_shows_detached_anchor` became
  `test_ratified_anchor_survives_extractor_ambiguity`.

**Still incomplete (by design)**

- Migration proposals for a changed extractor version (spec §14 criterion 4) are not
  emitted; v0.1 keeps the ratified identity and suppresses the re-derived sketch.
- Ratified requirements are still local (`.intentrace/log.jsonl`), not a tracked ledger.
- No evidence links, so `satisfied` and `locked` remain unprintable.

---

## FakeExtractor fake-v2 — splitter rework (migration event)

**Status:** complete.

**Delivered**

- `_split_sentences` v2: decimals/versions and abbreviations never split;
  `Class.method` references survive intact (resolving D4's unreachable branch
  as a consequence); wrapped lines join; list/table/heading items stay units;
  lone markers never end a segment. Offsets exact; provenance tests unweakened.
- **`VERSION` bumped `fake-v1` → `fake-v2`.** Segmentation changes spans, spans
  are in `req_id`, so every id changes — exactly the migration event design
  note §3 describes. Pre-bump ratifications become orphaned decisions;
  exercised end to end (`test_version_bump_orphans_old_ratifications`).
- **Migration itself remains unimplemented.** Orphaned decisions are reported
  (settle, why-fallback), never silently dropped or rebound.

---

## Slice 2 — the settle loop: ratification, and the first real drift

**Status:** complete.

**Delivered**

- **Decision log** (`Decision` with `kind: "ratify"` appended to the same JSONL; the kind
  union admits waive/demote/archive/migrate without a schema change). Read path
  discriminates by `dec_id`/`obs_id`; anything else is corruption.
- **Ratification carries the baseline**: each ratify decision records `symbol_path →
  node_hash` as scanned moments before the record. `intentrace ratify <req_id>`
  (unique prefix, `R-` display form accepted) shows the candidate, asks once, then
  re-validates before appending. Already-active is a no-op; unknown or ambiguous ids
  are errors. No `--all`, on either command (I2).
- **`intentrace settle`**: presents unratified sketches one at a time under one
  `settle_id`; y/n/q-or-EOF; empty queue says so and appends nothing. Orphaned
  decisions are printed, never dropped.
- **Maturity derived, not stored**: `apply_decisions` flips sketches to active from
  the earliest ratify; fresh extraction is a sketch until decisions replay.
- **Freshness (I3)**: `check_fresh` refuses proposals whose anchors changed or
  vanished since display, carrying the re-derived anchors so the CLI presents the
  current candidate instead of recording. Window this covers: the check compares the
  display-time table against the record-time table within one invocation (seconds).
  Sketches are not persisted, so there is no older proposal moment to be stale
  against — a days-old proposal the human remembers is outside what the tool can see.
- **Active-unimplemented requirements surface in `settle`.** A ratified anchorless
  requirement has no code location, so `why` cannot attach it anywhere (it reports
  "no intent covers this code" for want of a location, not want of intent). `settle`
  lists such requirements informationally; they are never queue items.
- **Drift verdicts in `why`**: active requirements judge each baseline path against
  current code — `unverified`, `unconfirmed` (naming baseline and current hashes),
  `orphaned` (symbol gone), `unimplemented` (ratified with no anchors). When nothing
  matches, active baselines and orphaned decisions concerning the target file are
  shown as fallback rather than hidden behind "no intent covers this code" (I4).
  `satisfied` and `locked` are never printed.
- **Design note §0 addendum**: identity is per utterance by construction;
  de-duplication would live at the queue level, never at identity; §2
  normalization currently changes no outcome.
- `build_symbol_table` moved from CLI to `symbol.py`; `ExtractionResult` moved to
  the port so the interface no longer lies about its return type.
- 77 tests (22 new, including planted-drift-caught and refactor-no-false-drift,
  written before the code); ruff and `mypy --strict` clean.
- Remediation: drift detail lines sorted (deterministic output), stale refusal
  asserts presentation, `R-` input form tested, anchorless-active surfacing tested;
  uncovered CLI branches covered, weak assertions strengthened, cleanup applied.
- Review follow-ups: record-time re-read rejects concurrent ratification (no file
  lock — closes the prompt-scale window, not microsecond TOCTOU); freshness
  re-extracts, so newly ambiguous/detached/newly attached anchors are stale;
  baseline-only verdict details sorted.

**Still incomplete (by design)**

- **No evidence, so no `satisfied`/`locked`, no gate.** Every active requirement
  prints `unverified` until evidence exists.
- **Migration proposals are not yet implemented** (specified in the design note).
- **No cross-cutting requirements, Python only.**

---

## Pre-slice-2 — requirement identity, and closing out slice 1

**Status:** complete (including remediation pass).

**Delivered**

- **Requirement identity design note** (`docs/design/requirement-identity.md`): settles what
  `req_id` covers (statement + provenance + extractor version; no anchors), statement
  normalization (hash-only — stored statement is verbatim), extractor_version in hash,
  splits/merges via `supersedes[]`, the alias table as `Decision(kind="migrate")` with cycle
  guard and chain compression, the migration proposal object, and the pending-migration queue
  for superseded requirements. SPEC §16 OPEN-1 is closed; §6.2 and §19 updated.
- **Deterministic symbol resolution**: `SymbolTable` keyed by qualified path. Bare-name lookup
  returns a sum type (`Resolved`, `Ambiguous`, `NotFound`). Ambiguous names carry the full
  candidate list with qualified paths; the CLI reports them. Two same-named functions in
  different files resolve deterministically.
- **Statement normalization is hash-only**: `_normalize_for_hash()` applies collapse
  whitespace, lowercase, and strip trailing punctuation to the hash input. The stored
  statement retains original case and punctuation. Three tests verify: stored text preserved,
  case/whitespace/punctuation diffs produce same id, must/should produces different ids.
- **Provenance spans tightened**: quoted slice has no leading/trailing whitespace.
- **Log reader record corrected**: docstring and D1 now honestly state that streaming was
  deferred, not that it streams.
- **`canonical_json` docstring corrected**: says what it does (JSON-encodes a list), not what
  it was meant to do (length-prefixed fields).
- **Type-checker workarounds removed**: `_collect_symbols` and related functions use proper
  tree-sitter types at module level. `_symbol_path` renamed to `symbol_path` (public).
- **Drift test renamed**: `test_node_hash_detects_drift` → `test_node_hash_changes_on_body_edit`
  (verifies the hash function, not system-level drift detection).
- **`_split_sentences` docstring corrected**: states offsets are unstripped and the caller
  tightens them. Standing rule added to DEVIATIONS.md: comments are part of behaviour changes.
- 55 tests; ruff and `mypy --strict` clean.

**Still incomplete (by design)**

- **Migration proposals are not yet implemented.** The design note defines the object and the
  workflow; the code to present and accept proposals lands in a later slice.
- **Pending-migration queue is defined but not implemented.** The design note specifies the
  state and the settle workflow; the code lands in a later slice.

---

## Slice 1 — `intentrace why`, end to end

**Status:** implemented; one review and remediation pass applied. Not a release.

**Delivered**

- Append-only JSONL observation log with torn-line and corruption detection.
- Content-addressed identity for observations and requirements, canonically encoded.
- `ExtractorPort` with one deterministic implementation, `FakeExtractor` — no model.
- tree-sitter anchoring for Python: repo-relative symbol paths, normalized subtree hashes
  that ignore comments, formatting and leading docstrings.
- In-memory store rebuilt from the log, behind a `Store` interface.
- `intentrace ingest` (dev-only) and `intentrace why`.
- 47 tests; ruff and `mypy --strict` clean.

**Deliberately out of scope**

Ratification and the decision log, the CI gate, reverse extraction and the unclaimed queue,
decay, waivers, evidence checks, SQLite indexing, TypeScript and C grammars, agent hooks and
transcript backfill, scope predicates and cross-cutting requirements.

**Known incomplete**

_(All items from slice 1's known-incomplete list have been addressed by pre-slice-2 work.)_

**Review findings** were tracked as F1–F11 and are closed.
