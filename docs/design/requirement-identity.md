# Requirement identity

**Status:** decided. Closes OPEN-1.

Slice 2 introduces the decision log: every ratification, waiver, and demotion references a
`req_id`. Once those records exist, changing how identity works stops being a schema edit and
becomes a migration of human decisions. This note settles identity while the cost of being
wrong is still zero.

---

## 1. What is inside `req_id`?

**Decision:** `req_id` is a content hash of:

- **Statement** (normalized, see §2)
- **Provenance spans** — each as `obs_id:start:end`
- **Extractor version**

Anchors are **excluded**.

**Rationale:** Identity captures *what the human intended*, not *where it currently lives*.
A requirement's identity should be invariant under refactoring — moving a function from
`retry.py` to `policies/retry.py` changes nothing about the intent. Including anchors would
mean identity depends on code location, which contradicts I6 (refactor invariance).

The counter-argument is real: excluding anchors means identity does not cover a field that
changes behaviour, so two runs can produce the same `req_id` with different anchors. This is
the leak found in the slice 1 review. But the leak is in *anchor stability*, not in
*identity*. Anchors are mutable metadata — they change when code moves, and they should.
Binding identity to them makes identity fragile; fixing anchor stability (Part B, B1) makes
anchors reliable without making them load-bearing for identity.

**What this forbids:**
- Using `req_id` to look up "which code does this requirement point to" — that requires
  joining on anchors, not hashing them.
- Assuming two requirements with the same `req_id` have the same anchors. They might not,
  if the code moved between extractions.
- Including any code-derived field (node_hash, file path, symbol path) in the identity
  hash. Identity is about intent; anchors are about location.

---

## 2. How is a statement normalized before hashing?

**Decision:** Normalize by:

1. **Collapse whitespace** — `" ".join(text.split())`
2. **Lowercase** — `lower()`
3. **Strip trailing punctuation** — remove trailing `.`, `!`, `?`

**Normalization applies only to the hash input.** The statement is stored and
displayed exactly as the human wrote it, modulo the whitespace collapse already
needed to make a provenance span a single line. The stored statement retains
original case, punctuation, and wording.

Unicode is **not** normalized (no NFKD/NFC). Case folding uses Python's default (Unicode
case mapping), which is correct for English and reasonable for most Latin-script languages.

**Rationale:** The goal is to decide when a trivial rewording forks identity and when it
silently collides. Both failure directions are real:

- **Over-normalization** (silent collision): "The system must retry." and "The system must
  retry" produce the same id. This is acceptable — the difference is formatting, not intent.
- **Under-normalization** (unnecessary fork): "The system must retry." and "The system
  should retry." produce different ids. This is correct — "must" and "should" have different
  normative weight and represent different intent.

Lowercasing catches "The system MUST retry" vs "The system must retry" as the same
requirement. Stripping trailing punctuation catches "retry." vs "retry" as the same.
Everything else is left to the extractor to present as distinct.

**What this forbids:**
- Semantic normalization (synonym expansion, stemming, lemmatization). That is extraction,
  not identity.
- Treating "must" and "should" as equivalent. They are different obligation levels.
- Treating "retry the operation" and "retry the operation." as different requirements.
- **Normalizing the stored or displayed statement.** The human wrote "Never expose stack
  traces to the user" and that is what `why` prints. Normalization is for the hash input
  only.

---

## 3. Does `extractor_version` stay in the hash?

**Decision:** Yes, `extractor_version` stays in the hash.

**Rationale:** This is the migration event by construction. When the extractor changes (new
model, new prompt, new version of the fake extractor), every requirement it produces gets a
new identity. This is exactly I1: a re-derivation proposes successors, never silently
replaces. If extractor_version were excluded, a new extractor could produce the same
`req_id` with a different statement or provenance — silent re-identification, which I1
forbids.

The cost is that extractor upgrades are always migration events, even when the output is
identical. This is acceptable: the migration is cheap (the alias table maps old→new) and the
guarantee is valuable (no silent changes).

**What this forbids:**
- Caching requirement identity across extractor versions. Each version produces fresh
  identities.
- Assuming two requirements from different extractor versions with the same statement have
  the same identity. They don't, and that's correct.
- Optimizing away the migration when output happens to be identical. The migration is
  idempotent but not skippable.

---

## 4. Splits and merges

**Decision:** Re-derivation turning one requirement into two is represented by the new
requirements having `supersedes: [old_req_id]`. Two requirements collapsing to one is
represented by the single new requirement having `supersedes: [old_id_1, old_id_2]`.

**Split example:** The fake extractor produces "The attempt function should retry on any
exception." A better extractor separates this into two: "The attempt function should retry
on transient exceptions." and "The attempt function should raise non-transient exceptions
immediately." Both have `supersedes: [old_id]`.

**Merge example:** Two requirements "Retry on errors" and "Handle failures gracefully" are
re-derived as one: "Retry on transient errors and raise others immediately." It has
`supersedes: [id1, id2]`.

**Rationale:** `supersedes[]` already exists in the spec model (§6.2). It is sufficient.
The reverse direction (two collapsing to one) uses the same field with multiple entries.

**What `supersedes[]` does NOT do:**
- It does not transfer ratifications automatically. A ratification belongs to the old
  `req_id`. The alias table (§5) records the mapping; the human confirms or rejects it.
- It does not imply the new requirement is ratified. It is a sketch until the human acts.
- It does not imply the old requirement is archived. The old requirement's maturity is
  unchanged until the human acts.

**What this forbids:**
- Auto-ratifying a successor. Re-derivation proposes; the human disposes (I1).
- Using `supersedes[]` as a ratification transfer mechanism. That is the alias table's job,
  and it requires a human decision record.
- Implicit archiving of superseded requirements. They remain active until explicitly
  archived.

---

## 5. The alias table

**Decision:** The alias table is a list of alias records, each with:

```
AliasRecord {
    old_req_id      str
    new_req_id      str           # the successor
    decision_id     str           # which Decision produced this mapping
    kind            supersede | merge
    timestamp       datetime
}
```

Stored in the JSONL log as a `Decision` with `kind: "migrate"`, carrying the alias record
in its fields.

**Resolution:** When `why` or `show` encounters an old `req_id` that is not in the current
requirement set, it looks up the alias table. If found, it follows the chain (old→new might
be multi-hop) and:

1. Prints the current requirement at the resolved id.
2. Prints a note: `migrated from R-{old_id[:8]} via decision {dec_id[:8]}`.
3. If the chain is ambiguous (one old id maps to multiple new ids — a split), it prints
  all successors.
4. A **cycle guard** detects loops (A→B→A) and stops traversal, returning the requirement
   at the last unvisited node. This prevents infinite traversal without bounding history.
5. **Chain compression** is applied at migration time: when a new migration maps
   old_id→current_id, the existing alias record for old_id→previous_id is updated to
   point directly to current_id. This keeps old→current a single hop regardless of how
   many migrations have occurred.

**Rationale:** The user pasting a stale id from an old PR comment must get an answer, not a
miss. The alias table is the bridge between old and new identities. Storing it as a
`Decision` in the log means it has the same immutability and auditability as ratifications.

Capping the *work* (cycle guard) rather than the *history* (depth limit) preserves full
auditability. A depth limit would manufacture misses after N upgrades — precisely the miss
§5 opens by promising to prevent. Chain compression at migration time keeps resolution
efficient without discarding history: the compressed path is derived, not deleted.

**What this forbids:**
- Deleting old requirement records when they are superseded. The log keeps everything.
- Making alias resolution silent. Every resolution prints the migration path.
- Allowing unbounded traversal work. A cycle guard terminates loops; chain compression
  keeps resolution O(1) after each migration.
- Imposing a depth limit that would cause stale ids to stop resolving after N upgrades.
  The human pasting an id from an old PR must always get an answer.

---

## 6. The migration proposal object

**Decision:** A migration proposal is a `MigrationProposal` (not yet a model — presented
as structured output to the human):

```
MigrationProposal {
    extractor_version     str
    old_requirements      list[Requirement]      # what is being replaced
    new_requirements      list[Requirement]       # what is proposed
    supersede_map         dict[old_id, new_id[]]  # which old maps to which new
    orphaned_evidence     list[EvidenceRef]       # evidence that has no successor
    timestamp             datetime
}
```

**What accepting a proposal does to the decision log:**

1. For each `old_id → new_id` mapping where the human confirms:
   - A `Decision(kind="migrate", req_id=old_id, ...)` is appended, carrying the alias
     record.
   - The old requirement's maturity is left unchanged (it was a sketch; it stays a sketch
     unless separately ratified).
2. For each new requirement that the human accepts:
   - It is added to the requirement set as a sketch.
   - If the human ratifies it, a separate `Decision(kind="ratify", req_id=new_id, ...)`
     is appended.
3. For orphaned requirements (old requirements with no successor in the new derivation):
   - The old requirement remains in the requirement set at its current maturity.
   - The human is warned: `R-{id[:8]} has no successor in the new derivation. It remains
     {maturity}.`
   - If the old requirement was ratified, its ratification is untouched — a ratified
     requirement without a successor is the human's decision to maintain or archive.

**Rationale:** Per I1, re-derivation cannot silently rebind ratifications. The proposal
presents the mapping; the human confirms. Orphaned ratifications are the critical case:
they are human decisions that now point at code the extractor no longer covers. The human
must see this and decide.

**What this forbids:**
- Auto-accepting migration proposals. Every mapping requires human confirmation.
- Auto-archiving orphaned ratified requirements. The human must decide.
- Discarding evidence from orphaned requirements. It is preserved and reported.
- Presenting migration proposals during `why`. They are only presented during `settle` or
  when explicitly requested.

---

## 6.5 Bounding requirement-set growth across migrations

**Problem:** §4 says superseded requirements are not archived. §6 says orphans stay at their
current maturity. Both are correct under I1 — and together they mean each migration leaves
the old and the new copy both active, with nothing stated about what clears the backlog.
Active requirements are what the gate reads, so the gate's input grows with every extractor
upgrade.

**Decision:** A superseded-but-unconfirmed requirement occupies a `pending_migration` queue.
The queue is a view, not a new maturity — the requirement's actual maturity is unchanged
(sketch, active, or locked). The queue filters for requirements that have a `supersedes[]`
reference from a newer requirement and whose `req_id` does not appear as the successor in
any alias record.

The human clears the queue during `settle`. For each entry the human sees:

1. The requirement at its current maturity.
2. The successor requirement(s) that supersede it.
3. The option to: (a) confirm the migration (creates the alias record, removes from queue),
   (b) reject the migration (removes the `supersedes[]` reference, removes from queue), or
   (c) defer (leaves the entry in the queue).

The `settle` command does not proceed until the queue is empty or the human explicitly
defers all entries. A deferred entry must be presented again at the next settle — it does
not disappear.

**Rationale:** "Not archived" is the right rule — I1 forbids silent disposal of human
decisions. But "not archived" is not an answer to "what bounds growth?" The pending-migration
queue makes the backlog visible and requires a human act to clear it. The gate reads the
active requirement set; the queue ensures superseded entries are surfaced and acted on
before the gate accumulates unbounded stale inputs.

**What this forbids:**
- Allowing superseded requirements to accumulate without human action. The queue must be
  cleared or explicitly deferred at each settle.
- Treating deferred entries as resolved. A deferred entry is re-presented at the next settle.
- Auto-confirming migrations. Every entry requires a human decision.

---

## Summary of decisions

| Question | Decision | Key constraint satisfied |
|---|---|---|
| What is in `req_id`? | Statement + provenance + extractor version. No anchors. | I6 (refactor invariance) |
| Statement normalization | Collapse whitespace, lowercase, strip trailing punctuation — hash input only; stored statement is verbatim | Balanced collision/fork trade-off |
| `extractor_version` in hash? | Yes | I1 (migration on version change) |
| Splits and merges | `supersedes[]` on the new requirement | I1 (proposal, not replacement) |
| Alias table | `Decision(kind="migrate")` in the log, with chain resolution | I1 (decision history preserved) |
| Migration proposal | Structured output, human confirms, orphans warned | I1, I2 (no silent rebinding) |

## Changes to SPEC

Authorized edits:

1. **§16 OPEN-1:** Mark as decided. Replace with a summary pointing to this design note.
2. **§6.2 Requirement:** Update the `req_id` description to state the fields and the
   exclusion of anchors.
3. **§19:** Add a row recording the change.
