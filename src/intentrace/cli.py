"""CLI entry point for intentrace.

Commands:
  intentrace ingest <observations.jsonl>  — dev-only; appends observations to the log
  intentrace why <path>[:<line>]          — show intent covering that code
  intentrace ratify <req_id> [--actor]    — ratify one requirement as an obligation
  intentrace settle [--actor]             — checkpoint: ratify or skip sketches one at a time
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from intentrace.anchor.python import (
    parse_source,
    resolve_line_to_node,
    symbol_path,
)
from intentrace.decisions import (
    ReqVerdict,
    Stale,
    apply_decisions,
    check_fresh,
    judge_requirement,
    match_req_id,
)
from intentrace.extract.fake import FakeExtractor
from intentrace.log import append_decision, append_observation, read_observations
from intentrace.models import Decision, Observation, Requirement
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

    view = apply_decisions(extraction.requirements, log_result.decisions)

    store = MemoryStore(repo_root)
    store.add_requirements(view.requirements)

    return _View(
        repo_root=repo_root,
        store=store,
        requirements=view.requirements,
        orphaned=view.orphaned,
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
        elif av.state == "detached" and av.baseline_hash and av.current_hash:
            lines.append(
                f"  drift      {av.symbol_path}: no longer attached by extractor "
                f"(baseline {av.baseline_hash[:8]}, current {av.current_hash[:8]})"
            )

    if req.evidence:
        evidence_strs = [f"{e.kind}:{e.ref}" for e in req.evidence]
        lines.append(f"  evidence   {', '.join(evidence_strs)}")
    else:
        lines.append("  evidence   none — nothing is checking this")

    return "\n".join(lines)


def _format_orphaned_decision(decision: Decision) -> str:
    """Format a decision whose req_id the extractor no longer produces."""
    lines: list[str] = []
    lines.append(f"R-{decision.req_id[:8]}   orphaned decision ({decision.kind})")
    ts = decision.timestamp.strftime("%Y-%m-%d")
    lines.append(f"  recorded   {ts}  {decision.actor}")
    concerned = ", ".join(sorted(decision.baseline_hashes)) or "—"
    lines.append(f"  concerned  {concerned}")
    lines.append("  note       no longer produced by extraction — see settle")
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
    return lines


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

    # Re-validate against current code before recording (I3): the baseline
    # must describe code the human has just seen, not an earlier proposal.
    fresh_symbols = build_symbol_table(repo_root)
    outcome = check_fresh(candidate, fresh_symbols)
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

    decision = Decision.create(
        kind="ratify",
        req_id=candidate.req_id,
        actor=actor,
        baseline_hashes=outcome.baseline,
    )
    append_decision(repo_root, decision)
    print(f"ratified {req_short}  baseline {len(outcome.baseline)} anchor(s)")
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
                f"no longer produced by extraction; concerned {concerned}"
            )
        print()

    sketches = [r for r in view.requirements if r.maturity == "sketch"]
    unimplemented = [r for r in view.requirements if r.maturity == "active" and not r.anchors]
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

    settle_id = datetime.now(UTC).strftime("settle-%Y%m%dT%H%M%S%f")
    ratified = 0
    skipped = 0
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
            print(f"settle stopped: {ratified} ratified, {skipped} skipped.")
            return 0
        if answer.strip().lower() not in ("y", "yes"):
            skipped += 1
            continue
        fresh_symbols = build_symbol_table(repo_root)
        outcome = check_fresh(sketch, fresh_symbols)
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
        append_decision(
            repo_root,
            Decision.create(
                kind="ratify",
                req_id=sketch.req_id,
                actor=actor,
                settle_id=settle_id,
                baseline_hashes=outcome.baseline,
            ),
        )
        print(f"ratified {req_short}")
        ratified += 1

    print(f"settle done: {ratified} ratified, {skipped} skipped.")
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

    # settle — checkpoint; presents sketches one at a time
    settle_parser = subparsers.add_parser(
        "settle", help="Checkpoint: ratify or skip sketches one at a time"
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
    elif args.command == "settle":
        return cmd_settle(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
