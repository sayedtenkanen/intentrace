# Progress

One entry per slice. What was built, what was deliberately left out, and what is known to be
incomplete. Slice briefs themselves are local working documents and are not published; this
file is the public record of what each slice actually delivered.

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

- **No persistence, so no drift detection across time.** Requirements are re-derived from the
  log on every invocation. The hash function is sensitive to behaviour changes, but the system
  retains no earlier baseline to compare against. Drift becomes detectable in slice 2, when
  ratification gives it a point to be measured from.
- **Migration proposals are not yet implemented.** The design note defines the object and the
  workflow; the code to present and accept proposals lands in slice 2.
- **Pending-migration queue is defined but not implemented.** The design note specifies the
  state and the settle workflow; the code lands in slice 2.

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
