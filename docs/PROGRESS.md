# Progress

One entry per slice. What was built, what was deliberately left out, and what is known to be
incomplete. Slice briefs themselves are local working documents and are not published; this
file is the public record of what each slice actually delivered.

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

- **No persistence, so no drift detection across time.** Requirements are re-derived from the
  log on every invocation and anchors are hashed against the source as it is at that moment.
  The hash function is sensitive to behaviour changes, but the system retains no earlier
  baseline to compare against. Drift becomes detectable in slice 2, when ratification gives
  it a point to be measured from.
- **The symbol table is keyed by bare symbol name across the whole repository, first match
  wins.** Two functions with the same name in different files collapse, and which one wins
  depends on filesystem traversal order. Requirement ids do not cover anchors, so two runs
  can produce identical ids with different anchors.
- Provenance spans bound the unstripped sentence window, so a quoted span can carry leading
  whitespace.
- The log reader still materialises all lines (recorded as D1 in `DEVIATIONS.md`).

**Review findings** were tracked as F1–F11 and are closed except where listed above.
