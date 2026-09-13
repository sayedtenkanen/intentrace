# intentrace

> Intent and code are two representations of the same system. They evolve independently and
> in either order. **intentrace keeps them reconciled, in both directions**, and reports the
> state of that reconciliation without ever overclaiming.

**Status: design in progress. There is no implementation yet.**
The artifact in this repository is [`docs/SPEC.md`](docs/SPEC.md).

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

R-0142   active · unverified
  "Transient failures are retried before surfacing to the caller."

  origin     co-derived
  said       2026-09-04  "don't fail on the first timeout, give it a few goes"
  built      diff 8f3a1c2, accepted 2026-09-04
  ratified   2026-09-06 by sayed
  anchors    src/retry.py::RetryPolicy::attempt
  evidence   none — nothing is checking this
```

Three things are deliberate in that output. The requirement quotes **your** words, not a
paraphrase. `ratified` is a separate date from `accepted`, because agreeing a diff looks
fine is not the same as taking responsibility for a behaviour. And `evidence: none` is
printed rather than omitted — a requirement nothing verifies is a fact you should see, not
a silence.

*Illustrative. Not implemented yet.*

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

**There is nothing to install.** No package, no CLI, no entry point — if you cloned this
expecting to run `intentrace`, stop here. The repository currently contains a design and
its history, nothing executable.

Pre-implementation. The first slice is `intentrace why <file>` working end to end against a
deterministic fake extractor, Python anchoring only, no model and no gate.

Eight decisions are open and marked as such in §16 of the spec, including requirement
identity under re-derivation and diff attribution across concurrent agent sessions.

## Prior art and neighbours

- **CodeSpeak** — [Structured Approach to Specs](https://codespeak.dev/blog/spindle-20260713),
  which prompted this design. Same diagnosis about prompts carrying intent; intentrace differs
  by treating requirement↔code trace and drift as the product rather than a future roadmap
  item, and by supporting code-first discovery as a first-class path.
- **Intentional Software** (Charles Simonyi) — two decades on making intent the primary
  artifact rather than a derived one.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
