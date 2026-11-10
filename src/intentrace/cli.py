"""CLI entry point for intentrace.

Commands:
  intentrace ingest <observations.jsonl>  — dev-only; appends observations to the log
  intentrace why <path>[:<line>]          — show intent covering that code
  intentrace ratify <req_id> [--actor]    — ratify one requirement as an obligation
  intentrace attest <req_id> [--anchor]   — record the exit for an open verdict
  intentrace settle [--actor]             — checkpoint: attest open verdicts, then sketches
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from intentrace.anchor.python import (
    parse_source,
    resolve_line_to_node,
    symbol_path,
)
from intentrace.decisions import (
    Attached,
    ReqVerdict,
    Stale,
    Suppressed,
    apply_decisions,
    check_fresh,
    judge_requirement,
    match_req_id,
)
from intentrace.extract.fake import FakeExtractor
from intentrace.log import append_decision, append_observation, read_observations
from intentrace.models import (
    AnchorRef,
    Decision,
    Observation,
    Requirement,
    RequirementSnapshot,
)
from intentrace.store import MemoryStore
from intentrace.symbol import SymbolTable, build_symbol_table


def _find_repo_root() -> Path:
    """Walk up from cwd to find a .intentrace directory or git repo."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".intentrace").exists() or (parent / ".git").exists():
            return parent
    return current


def _default_actor() -> str:
    """Who is acting, for the decision record.

    The login name is environment-derived, not fabricated: it names the
    account that ran the command. Overridable with --actor.
    """
    import getpass

    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _ask(prompt: str) -> str | None:
    """Prompt the human; None on EOF or interrupt (treated as decline/quit, never yes)."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


@dataclass
class _View:
    """One consistent read of log, extraction, decisions, and code."""

    repo_root: Path
    store: MemoryStore
    requirements: list[Requirement]  # annotated with maturity/ratification
    orphaned: list[Decision]
    suppressed: list[Suppressed]
    attached: list[Attached]
    unknown: list[Decision]
    symbols: SymbolTable


def _load_view(repo_root: Path) -> _View | None:
    """Read the log, re-extract, replay decisions. None on corrupt log."""
    log_result = read_observations(repo_root)
    if log_result.torn_line:
        print(f"warning: {log_result.torn_line}", file=sys.stderr)
    if log_result.corrupt_line:
        print(f"error: {log_result.corrupt_line}", file=sys.stderr)
        return None

    symbols = build_symbol_table(repo_root)
    extractor = FakeExtractor()
    extraction = extractor.extract(log_result.observations, symbols=symbols)

    for name, candidates in extraction.ambiguous_symbols.items():
        paths = ", ".join(candidates)
        print(f"warning: '{name}' resolves to multiple definitions: {paths}", file=sys.stderr)

    view = apply_decisions(extraction.requirements, log_result.decisions, symbols)

    store = MemoryStore(repo_root)
    store.add_requirements(view.requirements)

    return _View(
        repo_root=repo_root,
        store=store,
        requirements=view.requirements,
        orphaned=view.orphaned,
        suppressed=view.suppressed,
        attached=view.attached,
        unknown=view.unknown,
        symbols=symbols,
    )


def _observation_for(store: MemoryStore, req: Requirement) -> Observation | None:
    """Fetch the first provenance observation for display."""
    if req.provenance:
        return store.get_observation(req.provenance[0].obs_id)
    return None


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest observations from a JSONL file into the log."""
    repo_root = _find_repo_root()
    input_path = Path(args.observations_file)

    if not input_path.exists():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    with open(input_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"error: invalid JSON on line {line_num}", file=sys.stderr)
                return 1
            try:
                obs = Observation.model_validate(data)
            except ValidationError as e:
                print(
                    f"error: invalid observation on line {line_num}: {e}",
                    file=sys.stderr,
                )
                return 1
            append_observation(repo_root, obs)

    return 0


def _format_requirement(req: Requirement, obs: Observation | None, verdict: ReqVerdict) -> str:
    """Format a requirement for display.

    Maturity is derived from the decision log; status from the drift
    verdict. satisfied and locked are never printed: evidence does not
    exist yet, so no requirement can have earned them (I5).
    """
    lines: list[str] = []
    req_short = f"R-{req.req_id[:8]}"
    lines.append(f"{req_short}   {req.maturity} · {verdict.status}")
    lines.append(f'  "{req.statement}"')
    lines.append("")
    lines.append(f"  origin     {req.origin}")

    # Show quoted span from the observation text (the human's words)
    if obs and req.provenance:
        span = req.provenance[0]
        quoted = obs.text[span.start : span.end].strip()
        if len(quoted) > 60:
            quoted = quoted[:57] + "..."
        lines.append(f'  quoted     "{quoted}"')
        ts = obs.timestamp.strftime("%Y-%m-%d")
        session_turn = f"session {obs.session_id[:4]} · turn {obs.turn_index}"
        lines.append(f"  said       {ts}  {session_turn}")

    if req.ratification is not None:
        rts = req.ratification.timestamp.strftime("%Y-%m-%d")
        lines.append(f"  ratified   {rts}  {req.ratification.actor}")
        if req.ratification.attested_at is not None:
            ats = req.ratification.attested_at.strftime("%Y-%m-%d")
            lines.append(
                f"  attested   {ats}  {req.ratification.attest_actor} · "
                f"{req.ratification.attest_reason}"
            )
    else:
        lines.append("  ratified   —")

    if req.anchors:
        anchor_strs = [a.symbol_path for a in req.anchors]
        lines.append(f"  anchors    {', '.join(anchor_strs)}")
    else:
        lines.append("  anchors    none")

    for av in verdict.anchors:
        if av.state == "changed" and av.baseline_hash and av.current_hash:
            lines.append(
                f"  drift      {av.symbol_path}: "
                f"baseline {av.baseline_hash[:8]} → current {av.current_hash[:8]}"
            )
        elif av.state == "missing":
            lines.append(f"  orphaned   anchor {av.symbol_path} no longer resolves")
        elif av.state == "pending" and av.current_hash:
            lines.append(
                f"  pending    {av.symbol_path}: no baseline — attest to cover it "
                f"(current {av.current_hash[:8]})"
            )

    if req.evidence:
        evidence_strs = [f"{e.kind}:{e.ref}" for e in req.evidence]
        lines.append(f"  evidence   {', '.join(evidence_strs)}")
    else:
        lines.append("  evidence   none — nothing is checking this")

    return "\n".join(lines)


def _orphan_reason(decision: Decision) -> str:
    """Why this decision has no active requirement to attach to."""
    if decision.kind == "ratify" and decision.snapshot is None:
        return (
            "ratified before snapshots were stored — the statement was not "
            "recorded; re-ratify the sketch if it is still proposed"
        )
    return "no active ratification has this id"


def _format_orphaned_decision(decision: Decision) -> str:
    """Format a decision whose req_id has no active requirement."""
    lines: list[str] = []
    lines.append(f"R-{decision.req_id[:8]}   orphaned decision ({decision.kind})")
    ts = decision.timestamp.strftime("%Y-%m-%d")
    lines.append(f"  recorded   {ts}  {decision.actor}")
    concerned = ", ".join(sorted(decision.baseline_hashes)) or "—"
    lines.append(f"  concerned  {concerned}")
    lines.append(f"  reason     {_orphan_reason(decision)}")
    return "\n".join(lines)


def _stale_lines(req_short: str, outcome: Stale, candidate: Requirement) -> list[str]:
    """Explain why a proposal cannot be ratified as-is (I3)."""
    old_hashes = {a.symbol_path: a.node_hash for a in candidate.anchors}
    new_hashes = {a.symbol_path: a.node_hash for a in outcome.fresh_anchors}
    lines = [f"cannot ratify {req_short}: code changed since proposal"]
    for path in outcome.changed:
        lines.append(f"  {path}: proposed {old_hashes[path][:8]} → current {new_hashes[path][:8]}")
    for path in outcome.missing:
        lines.append(f"  {path}: no longer resolves")
    for path in outcome.detached:
        lines.append(f"  {path}: no longer attached by extractor")
    for path in outcome.attached:
        lines.append(f"  {path}: newly attached by extractor")
    return lines


def _record_ratification(
    repo_root: Path,
    req_id: str,
    actor: str,
    settle_id: str,
    baseline: dict[str, str],
    baseline_name_hashes: dict[str, str],
    snapshot: RequirementSnapshot,
) -> Decision | None:
    """Append a ratify decision unless one landed while deciding.

    Re-reads the log immediately before appending: a requirement that
    became active after the view was built (a concurrent ratify or settle)
    is rejected instead of recorded twice. Returns the appended decision,
    or None if the requirement is already active.

    This closes the minutes-scale window of a human answering a prompt,
    not a microsecond-scale race: read-check-append is not atomic, and no
    file lock is taken (the log must stay portable across platforms).
    """
    fresh = read_observations(repo_root)
    # Only a snapshot-bearing ratify makes a requirement active. A decision
    # written before snapshots existed must not block re-ratifying the same
    # sketch: that would leave an orphaned requirement with no exit.
    if any(
        d.kind == "ratify" and d.req_id == req_id and d.snapshot is not None
        for d in fresh.decisions
    ):
        return None
    decision = Decision.create(
        kind="ratify",
        req_id=req_id,
        actor=actor,
        settle_id=settle_id,
        baseline_hashes=baseline,
        baseline_name_hashes=baseline_name_hashes,
        snapshot=snapshot,
    )
    append_decision(repo_root, decision)
    return decision


def cmd_ratify(args: argparse.Namespace) -> int:
    """Ratify one requirement: the deliberate act (I2), freshly baselined (I3)."""
    repo_root = _find_repo_root()
    actor = args.actor or _default_actor()
    view = _load_view(repo_root)
    if view is None:
        return 1

    matches = match_req_id(args.req_id, view.requirements)
    if not matches:
        print(f"error: no sketch with id '{args.req_id}'", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(
            f"error: prefix '{args.req_id}' matches {len(matches)} requirements; be more specific",
            file=sys.stderr,
        )
        return 1
    candidate = matches[0]
    req_short = f"R-{candidate.req_id[:8]}"

    if candidate.maturity == "active" and candidate.ratification is not None:
        rts = candidate.ratification.timestamp.strftime("%Y-%m-%d")
        print(f"{req_short} is already active (ratified {rts} by {candidate.ratification.actor})")
        return 0

    print(
        _format_requirement(
            candidate,
            _observation_for(view.store, candidate),
            judge_requirement(candidate, view.symbols),
        )
    )
    print()
    answer = _ask(f"Ratify {req_short} as an obligation? [y/N] ")
    if answer is None or answer.strip().lower() not in ("y", "yes"):
        print("not ratified")
        return 0

    # Re-validate against a fresh extraction before recording (I3): the
    # baseline must describe code the human has just seen, not an earlier
    # proposal. Re-extraction (not just a hash comparison) catches anchors
    # the extractor would no longer attach, e.g. newly ambiguous names.
    fresh_symbols = build_symbol_table(repo_root)
    fresh_reqs = (
        FakeExtractor().extract(view.store.all_observations, symbols=fresh_symbols).requirements
    )
    outcome = check_fresh(candidate, fresh_symbols, fresh_reqs)
    if isinstance(outcome, Stale):
        for line in _stale_lines(req_short, outcome, candidate):
            print(line)
        print()
        print("Re-derived candidate:")
        refreshed = candidate.model_copy(update={"anchors": outcome.fresh_anchors})
        print(
            _format_requirement(
                refreshed,
                _observation_for(view.store, candidate),
                judge_requirement(refreshed, fresh_symbols),
            )
        )
        return 1

    snapshot = RequirementSnapshot(
        statement=candidate.statement,
        provenance=candidate.provenance,
        extractor_version=candidate.derivation.extractor_version,
    )
    decision = _record_ratification(
        repo_root,
        candidate.req_id,
        actor,
        "",
        outcome.baseline,
        outcome.baseline_name_hashes,
        snapshot,
    )
    if decision is None:
        print(f"{req_short} is already active — not recorded twice")
        return 0
    print(f"ratified {req_short}  baseline {len(outcome.baseline)} anchor(s)")
    return 0


@dataclass(frozen=True)
class _AttestPlan:
    """The baseline an attestation would record, and the reason it earns."""

    baseline: dict[str, str]
    name_baseline: dict[str, str]
    reason: str
    renamed: dict[str, str] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)


def _plan_attest(
    req: Requirement, symbols: SymbolTable, extra_paths: list[str] | None = None
) -> tuple[_AttestPlan | None, str]:
    """Plan one attestation, or say why it cannot be planned.

    The tool assigns the reason from what it observes: a changed body, or a
    vanished anchor with no replacement, is drift-accepted (a rationale is
    required); a missing anchor replaced by a same-file match is a rename
    (different file: move); and an anchor with no baseline is a late anchor.
    The human cannot pick a cheaper reason.

    Every missing anchor must be covered by the plan. A manual --anchor
    list must have one entry per missing anchor, so attesting one rename
    cannot silently drop another anchor from the baseline. With no --anchor,
    a missing anchor that has exactly one same-file match is proposed as a
    rename; one with no match is shown as dropped and needs the drift
    rationale; more than one match needs a manual --anchor.
    """
    if req.ratification is None:
        return None, "not a ratified requirement"
    baseline = req.ratification.baseline_hashes
    names = req.ratification.baseline_name_hashes

    changed = [
        path
        for path, old_hash in baseline.items()
        if path in symbols.by_qualified and symbols.by_qualified[path].node_hash != old_hash
    ]
    missing = sorted(path for path in baseline if path not in symbols.by_qualified)

    extras: list[AnchorRef] = []
    for path in extra_paths or []:
        extra = symbols.by_qualified.get(path)
        if extra is None:
            return None, f"no symbol at {path}"
        if extra.symbol_path in baseline:
            return None, f"{path} is already anchored"
        if any(a.symbol_path == path for a in extras):
            return None, f"duplicate --anchor {path}"
        extras.append(extra)

    anchors = {a.symbol_path: a for a in req.anchors}
    renamed: dict[str, str] = {}
    dropped: list[str] = []

    if missing:
        if extras:
            if len(extras) != len(missing):
                return None, (
                    f"{len(missing)} anchor(s) are missing; pass one --anchor per missing anchor"
                )
            remaining = list(missing)
            assigned: dict[str, AnchorRef] = {}
            for extra in extras:
                matches = [old for old in remaining if names.get(old) == extra.name_hash]
                if len(matches) == 1:
                    old = matches[0]
                elif len(missing) == 1 and len(extras) == 1:
                    old = missing[0]
                else:
                    return None, (
                        f"cannot tell which missing anchor --anchor {extra.symbol_path} "
                        "replaces; pass one --anchor per missing anchor"
                    )
                assigned[old] = extra
                remaining.remove(old)
            for old, anchor in assigned.items():
                renamed[old] = anchor.symbol_path
                anchors[anchor.symbol_path] = anchor
        else:
            proposals: dict[str, AnchorRef] = {}
            for old in missing:
                file = old.split("::", 1)[0]
                name_hash = names.get(old)
                if not name_hash:
                    return None, f"no rename hash recorded for {old}; pass --anchor"
                candidates = [
                    a
                    for a in symbols.by_qualified.values()
                    if a.file == file and a.name_hash == name_hash and a.symbol_path != old
                ]
                if len(candidates) == 0:
                    dropped.append(old)
                    continue
                if len(candidates) > 1:
                    return None, f"{len(candidates)} rename candidates for {old}; pass --anchor"
                proposals[old] = candidates[0]
            resolved = {a.symbol_path for a in proposals.values()}
            if len(resolved) != len(proposals):
                return None, "rename candidates are not distinct; pass --anchor"
            for old, anchor in proposals.items():
                renamed[old] = anchor.symbol_path
                anchors[anchor.symbol_path] = anchor
    elif extras:
        for extra in extras:
            anchors[extra.symbol_path] = extra

    pending = sorted(path for path in anchors if path not in baseline)
    # A vanished anchor with no replacement is accepted drift: the human
    # acknowledges that the implementation is gone, and must give a reason.
    if changed or dropped:
        reason = "drift-accepted"
    elif renamed:
        same_file = all(
            old.split("::", 1)[0] == new.split("::", 1)[0] for old, new in renamed.items()
        )
        reason = "rename" if same_file else "move"
    elif pending:
        reason = "late-anchor"
    else:
        return None, "nothing to attest"

    return (
        _AttestPlan(
            baseline={path: anchor.node_hash for path, anchor in anchors.items()},
            name_baseline={path: anchor.name_hash for path, anchor in anchors.items()},
            reason=reason,
            renamed=renamed,
            dropped=dropped,
        ),
        "",
    )


def cmd_attest(args: argparse.Namespace) -> int:
    """Record the exit for an open verdict: late anchor, drift, rename or move."""
    repo_root = _find_repo_root()
    actor = args.actor or _default_actor()
    view = _load_view(repo_root)
    if view is None:
        return 1

    active = [r for r in view.requirements if r.maturity == "active"]
    matches = match_req_id(args.req_id, active)
    if not matches:
        print(f"error: no active requirement with id '{args.req_id}'", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(
            f"error: prefix '{args.req_id}' matches {len(matches)} requirements; be more specific",
            file=sys.stderr,
        )
        return 1
    req = matches[0]
    req_short = f"R-{req.req_id[:8]}"

    plan, error = _plan_attest(req, view.symbols, args.anchor)
    if plan is None:
        print(f"error: cannot attest {req_short}: {error}", file=sys.stderr)
        return 1
    rationale = args.rationale
    if plan.reason == "drift-accepted" and not (rationale and rationale.strip()):
        print(
            "error: accepting drift requires --rationale",
            file=sys.stderr,
        )
        return 1

    print(
        _format_requirement(
            req,
            _observation_for(view.store, req),
            judge_requirement(req, view.symbols),
        )
    )
    print()
    for old, new in sorted(plan.renamed.items()):
        print(f"  propose    {old} → {new}")
    for old in sorted(plan.dropped):
        print(f"  drop       {old} — no replacement found")
    print(f"  attest     {plan.reason}: {', '.join(sorted(plan.baseline)) or '—'}")
    print()
    answer = _ask(f"Attest {req_short}? [y/N] ")
    if answer is None or answer.strip().lower() not in ("y", "yes"):
        print("not attested")
        return 0

    decision = Decision.create(
        kind="attest",
        req_id=req.req_id,
        actor=actor,
        rationale=rationale,
        baseline_hashes=plan.baseline,
        baseline_name_hashes=plan.name_baseline,
        reason=plan.reason,
    )
    append_decision(repo_root, decision)
    print(f"attested {req_short} ({plan.reason})")
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    """Checkpoint: present unratified sketches one at a time (I2: no bulk)."""
    repo_root = _find_repo_root()
    actor = args.actor or _default_actor()
    view = _load_view(repo_root)
    if view is None:
        return 1

    if view.orphaned:
        print("orphaned decisions:")
        for d in view.orphaned:
            concerned = ", ".join(sorted(d.baseline_hashes)) or "—"
            ts = d.timestamp.strftime("%Y-%m-%d")
            print(
                f"  R-{d.req_id[:8]} {d.kind} by {d.actor} on {ts} — "
                f"{_orphan_reason(d)}; concerned {concerned}"
            )
        print()

    if view.unknown:
        print("decisions recorded by a newer version (ignored):")
        for d in view.unknown:
            ts = d.timestamp.strftime("%Y-%m-%d")
            print(f"  R-{d.req_id[:8]} {d.kind} by {d.actor} on {ts}")
        print()

    settle_id = datetime.now(UTC).strftime("settle-%Y%m%dT%H%M%S%f")
    ratified = 0
    skipped = 0
    attested = 0

    open_items = []
    for req in view.requirements:
        if req.maturity != "active":
            continue
        verdict = judge_requirement(req, view.symbols)
        if verdict.status in ("unconfirmed", "orphaned"):
            open_items.append((req, verdict))

    if open_items:
        print("open verdicts to attest:")
        for req, verdict in open_items:
            req_short = f"R-{req.req_id[:8]}"
            print(_format_requirement(req, _observation_for(view.store, req), verdict))
            plan, error = _plan_attest(req, view.symbols)
            if plan is None:
                print(
                    f"  manual attest required: intentrace attest {req_short} "
                    f"--anchor <symbol_path>"
                )
                print(f"  ({error})")
                print()
                skipped += 1
                continue
            for old, new in sorted(plan.renamed.items()):
                print(f"  propose    {old} → {new}")
            for old in sorted(plan.dropped):
                print(f"  drop       {old} — no replacement found")
            print(f"  proposed   {plan.reason}: {', '.join(sorted(plan.baseline)) or '—'}")
            answer = _ask(f"Attest {req_short}? [y(es)/N(o)/q(uit)] ")
            if answer is None or answer.strip().lower() in ("q", "quit"):
                print(
                    f"settle stopped: {attested} attested, {ratified} ratified, {skipped} skipped."
                )
                return 0
            if answer.strip().lower() not in ("y", "yes"):
                skipped += 1
                continue
            rationale = None
            if plan.reason == "drift-accepted":
                rationale = _ask("Rationale for accepting the drift: ")
                if rationale is None or not rationale.strip():
                    print("not attested — a rationale is required for drift")
                    skipped += 1
                    continue
            append_decision(
                repo_root,
                Decision.create(
                    kind="attest",
                    req_id=req.req_id,
                    actor=actor,
                    settle_id=settle_id,
                    rationale=rationale,
                    baseline_hashes=plan.baseline,
                    baseline_name_hashes=plan.name_baseline,
                    reason=plan.reason,
                ),
            )
            print(f"attested {req_short} ({plan.reason})")
            attested += 1
        print()

    if view.suppressed:
        print(f"suppressed sketches: {len(view.suppressed)} — already covered by a ratification")
        for s in view.suppressed:
            print(f'  R-{s.snapshot_req_id[:8]} covers "{s.sketch.statement[:50]}" ({s.reason})')
        print()

    if view.attached:
        print(f"attached sketches: {len(view.attached)} — shown, not proposed as new")
        for a in view.attached:
            print(f'  R-{a.snapshot_req_id[:8]} · {a.reason}: "{a.sketch.statement[:50]}"')
        print()

    sketches = [r for r in view.requirements if r.maturity == "sketch"]
    # Only a requirement that never had an anchor is unimplemented. An
    # orphaned requirement has an empty anchor set too, and listing it here
    # would contradict its own verdict.
    unimplemented = [
        r
        for r in view.requirements
        if r.maturity == "active" and judge_requirement(r, view.symbols).status == "unimplemented"
    ]
    if unimplemented:
        # Informational only: these are obligations with no code location,
        # so `why` cannot attach them anywhere and they are never queue items.
        print("active requirements with no anchors (unimplemented — nothing to ratify):")
        for r in unimplemented:
            print(f'  R-{r.req_id[:8]} "{r.statement}"')
        print()
    if not sketches:
        print("nothing to settle — no unratified sketches")
        return 0

    for sketch in sketches:
        req_short = f"R-{sketch.req_id[:8]}"
        print(
            _format_requirement(
                sketch,
                _observation_for(view.store, sketch),
                judge_requirement(sketch, view.symbols),
            )
        )
        answer = _ask(f"Ratify {req_short}? [y(es)/N(o)/q(uit)] ")
        if answer is None or answer.strip().lower() in ("q", "quit"):
            print(f"settle stopped: {attested} attested, {ratified} ratified, {skipped} skipped.")
            return 0
        if answer.strip().lower() not in ("y", "yes"):
            skipped += 1
            continue
        fresh_symbols = build_symbol_table(repo_root)
        fresh_reqs = (
            FakeExtractor()
            .extract(view.store.all_observations, symbols=fresh_symbols)
            .requirements
        )
        outcome = check_fresh(sketch, fresh_symbols, fresh_reqs)
        if isinstance(outcome, Stale):
            for line in _stale_lines(req_short, outcome, sketch):
                print(line)
            print()
            print("Re-derived candidate:")
            refreshed = sketch.model_copy(update={"anchors": outcome.fresh_anchors})
            print(
                _format_requirement(
                    refreshed,
                    _observation_for(view.store, sketch),
                    judge_requirement(refreshed, fresh_symbols),
                )
            )
            skipped += 1
            continue
        snapshot = RequirementSnapshot(
            statement=sketch.statement,
            provenance=sketch.provenance,
            extractor_version=sketch.derivation.extractor_version,
        )
        decision = _record_ratification(
            repo_root,
            sketch.req_id,
            actor,
            settle_id,
            outcome.baseline,
            outcome.baseline_name_hashes,
            snapshot,
        )
        if decision is None:
            print(f"{req_short} became active while confirming — skipped.")
            skipped += 1
            continue
        print(f"ratified {req_short}")
        ratified += 1

    print(f"settle done: {attested} attested, {ratified} ratified, {skipped} skipped.")
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    """Show intent covering a code location."""
    repo_root = _find_repo_root()
    target = args.target

    # Parse path:line
    if ":" in target:
        path_str, line_str = target.rsplit(":", 1)
        try:
            line_num = int(line_str)
        except ValueError:
            print(f"error: invalid line number: {line_str}", file=sys.stderr)
            return 1
    else:
        path_str = target
        line_num = None

    file_path = Path(path_str)
    if not file_path.exists():
        # Try relative to repo root
        file_path = repo_root / path_str
    if not file_path.exists():
        print(f"error: file not found: {path_str}", file=sys.stderr)
        return 1

    source = file_path.read_bytes()
    if file_path.is_relative_to(repo_root):
        repo_rel = str(file_path.relative_to(repo_root))
    else:
        repo_rel = path_str

    # Parse the source
    tree = parse_source(source)

    view = _load_view(repo_root)
    if view is None:
        return 1

    if not view.store.all_observations:
        print(
            "no observations recorded — run 'intentrace ingest' first",
            file=sys.stderr,
        )
        return 1

    # Find which symbol encloses the target line
    target_symbol: str | None = None
    if line_num is not None:
        node = resolve_line_to_node(tree, line_num, source)
        if node is not None and node.type != "module":
            target_symbol = symbol_path(repo_rel, node, source)

    # Find matching requirements by reading stored anchors only
    found: list[tuple[Requirement, Observation | None, ReqVerdict]] = []
    seen_reqs: set[str] = set()

    for req in view.requirements:
        for anchor in req.anchors:
            if (
                anchor.file == repo_rel
                and (target_symbol is None or anchor.symbol_path == target_symbol)
                and req.req_id not in seen_reqs
            ):
                verdict = judge_requirement(req, view.symbols)
                found.append((req, _observation_for(view.store, req), verdict))
                seen_reqs.add(req.req_id)

    # Fallback: when nothing matches, surface active requirements and
    # orphaned decisions whose ratified baseline concerns this file but no
    # longer resolves cleanly. A ratified decision about this code must
    # never hide behind "no intent covers this code" (I4).
    fallback_decisions: list[Decision] = []
    if not found:
        for req in view.requirements:
            if (
                req.req_id in seen_reqs
                or req.maturity != "active"
                or req.ratification is None
                or not any(p.startswith(repo_rel + "::") for p in req.ratification.baseline_hashes)
            ):
                continue
            verdict = judge_requirement(req, view.symbols)
            if verdict.status == "unverified":
                continue
            found.append((req, _observation_for(view.store, req), verdict))
            seen_reqs.add(req.req_id)
        for d in view.orphaned:
            if any(p.startswith(repo_rel + "::") for p in d.baseline_hashes):
                fallback_decisions.append(d)

    if not found and not fallback_decisions:
        print("no intent covers this code")
        return 0

    for req, obs, verdict in found:
        print(_format_requirement(req, obs, verdict))
        print()
    for d in fallback_decisions:
        print(_format_orphaned_decision(d))
        print()

    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="intentrace")
    subparsers = parser.add_subparsers(dest="command")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="Ingest observations from a JSONL file")
    ingest_parser.add_argument("observations_file", help="Path to observations JSONL file")

    # why
    why_parser = subparsers.add_parser("why", help="Show intent covering a code location")
    why_parser.add_argument("target", help="path:line to inspect")

    # ratify — one requirement, one deliberate act; never bulk (I2)
    ratify_parser = subparsers.add_parser("ratify", help="Ratify one requirement as an obligation")
    ratify_parser.add_argument("req_id", help="req_id or unique prefix")
    ratify_parser.add_argument(
        "--actor", default=None, help="who is ratifying (defaults to login name)"
    )

    # attest — the recorded exit for a late anchor, drift, rename or move
    attest_parser = subparsers.add_parser(
        "attest", help="Record the exit for an open verdict (late anchor, drift, rename)"
    )
    attest_parser.add_argument("req_id", help="req_id or unique prefix of an active requirement")
    attest_parser.add_argument(
        "--anchor",
        action="append",
        default=None,
        help=(
            "qualified symbol path to attest onto; repeat once per missing anchor "
            "(defaults to a unique rename candidate)"
        ),
    )
    attest_parser.add_argument(
        "--rationale",
        default=None,
        help="why drift is accepted (required when a body hash changed)",
    )
    attest_parser.add_argument(
        "--actor", default=None, help="who is attesting (defaults to login name)"
    )

    # settle — checkpoint; presents open verdicts and sketches one at a time
    settle_parser = subparsers.add_parser(
        "settle", help="Checkpoint: attest open verdicts, then ratify or skip sketches"
    )
    settle_parser.add_argument(
        "--actor", default=None, help="who is ratifying (defaults to login name)"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "ingest":
        return cmd_ingest(args)
    elif args.command == "why":
        return cmd_why(args)
    elif args.command == "ratify":
        return cmd_ratify(args)
    elif args.command == "attest":
        return cmd_attest(args)
    elif args.command == "settle":
        return cmd_settle(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
