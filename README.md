# intentrace

> Intent and code are two representations of the same system. They evolve independently and
> in either order. **intentrace keeps them reconciled, in both directions**, and reports the
> state of that reconciliation without ever overclaiming.

**Status: slices 1–2 implemented.** `intentrace why` reports intent covering code,
`intentrace ratify` / `intentrace settle` record human ratifications with code
baselines, and `why` reports drift for ratified requirements. No model, no gate yet.
The artifact behind the implementation is [`docs/SPEC.md`](docs/SPEC.md).

---

## Why

Agentic coding produces four failures that compound: you don't know how your own code works,
there is too much of it to review, the original intent is recoverable only by reading the
implementation, and agents break working behaviour while adding new behaviour.

Spec-Driven Development answers this by making a human-written specification the source of
truth. It carries one structural assumption — **that intent precedes implementation** — and
that assumption is false often enough to matter. People explore in code, discover what they
meant, and settle the requirement afterwards. A tool that demands the requirement first gets
abandoned during exactly the phase where it would help most.

intentrace does not demand anything up front.

## What it does

- **Captures intent where it is actually produced** — the prompts you already write — and
  where it is actually settled: the code you accepted.
- **Maintains requirements linked to code in both directions.** Requirements can be
  *declared* (from what you said), *recovered* (from what you built), or co-derived from both.
- **Reports drift** from the point a human took responsibility for a requirement — not from
  whatever the code happens to say now.
- **Keeps two symmetric queues**: requirements with no code, and code no requirement
  explains. Working either one down is progress, and you can start from either end.
- **Gates CI** on conditions you can defend, including the absence of reconciliation itself.

## What `intentrace why` shows you

The first command, and the clearest illustration of the model. You point it at code and it
tells you what intent covers that code, where the intent came from, and whether anything is
verifying it:

```
$ intentrace why src/retry.py:42

R-414282bb   sketch · unverified
  "The attempt function should retry on any exception."

  origin     declared
  quoted     "The attempt function should retry on any exception."
  said       2026-09-04  session sess · turn 3
  ratified   —
  anchors    src/retry.py::RetryPolicy::attempt
  evidence   none — nothing is checking this
```

Three things are deliberate in that output. The requirement quotes **your** words, not a
paraphrase. `ratified` is printed even when nothing has been ratified, because agreeing a
diff looks fine is not the same as taking responsibility for a behaviour — and until
someone has, the dash is the honest answer. And `evidence: none` is printed rather than
omitted: a requirement nothing verifies is a fact you should see, not a silence.

Later slices add the states this cannot yet reach — `active` and a real `ratified` date
arrive with the settle loop, `co-derived` origin with reverse extraction.

## What it does not do

- It does not generate or modify code.
- It does not ask anyone to author a specification document.
- It does not claim your code is correct. It reports reconciliation state, and it is designed
  to be unable to report health it cannot justify.
- No ReqIF / ASPICE export yet. Deferred, not rejected.

## Design rules

The spec is built on nine invariants. They are honesty rules: a feature that violates any
one of them is out of scope by definition. The most consequential:

- **Ratification is a separate act from accepting a diff.** Accepting means "this looks
  fine"; ratifying means "this is now an obligation". If those ever collapse into one click,
  the tool becomes a machine for retroactively justifying whatever the agent wrote.
- **Drift is measured from ratification**, and a candidate is re-validated against current
  code at the moment it is ratified.
- **No green without evidence** — and a repository where nothing has been settled must not
  report the same result as one that reconciles. "Nothing is checked" is not "clean".
- **A well-formed requirement is invariant under refactoring and violated by behaviour
  change.** This is enforced online by alpha-rename equivalence: rename the identifiers in a
  diff, extract twice, and reject any candidate that changed — it was restating the
  implementation rather than the intent.
- **Dangling anchors are events, never silently re-attached.**

The full set, with the reasoning and the open problems, is in [`docs/SPEC.md`](docs/SPEC.md)
§5. [`docs/SPEC.v0.1.md`](docs/SPEC.v0.1.md) is kept so the design history is readable; §19
of the current spec lists what changed between them and which finding forced each change.

## Status and how to run it

Slice 1 (`why` end to end against a deterministic fake extractor) and slice 2 (the
settle loop: ratification with code baselines, drift verdicts) are implemented and
have been through review. Python anchoring only, no model and no gate. Against the
bundled fixture:

```
uv sync --extra dev
uv run intentrace ingest tests/fixtures/sample_project/observations.jsonl
uv run intentrace why tests/fixtures/sample_project/retry.py:11
uv run intentrace settle            # ratify or skip sketches one at a time
uv run intentrace ratify R-414282bb # ratify one requirement (unique prefix works)
uv run intentrace why tests/fixtures/sample_project/retry.py:11   # now active
```

**What it can and cannot do yet.** Drift across time now works for ratified
requirements: ratification records each anchor's `node_hash`, and `why` compares
current code against that baseline — a changed body reports `unconfirmed` naming
both hashes, a vanished symbol reports `orphaned`, anything else stays
`unverified`. What is still missing: no evidence and therefore no `satisfied` or
`locked` states, no CI gate, no cross-cutting requirements (scope predicates),
Python only, and migration is specified but not executed.

Divergences from the slice brief are recorded in [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md),
and per-slice status in [`docs/PROGRESS.md`](docs/PROGRESS.md).

Six decisions are open and marked as such in §16 of the spec, including diff
attribution across concurrent agent sessions.

## Prior art and neighbours

- **CodeSpeak** — [Structured Approach to Specs](https://codespeak.dev/blog/spindle-20260713),
  which prompted this design. Same diagnosis about prompts carrying intent; intentrace differs
  by treating requirement↔code trace and drift as the product rather than a future roadmap
  item, and by supporting code-first discovery as a first-class path.
- **Intentional Software** (Charles Simonyi) — two decades on making intent the primary
  artifact rather than a derived one.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
