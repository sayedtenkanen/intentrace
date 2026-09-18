"""Decision replay and freshness for the settle loop (slice 2).

Requirements remain a derived view: re-extracted from observations on each
run. Decisions persist in the log and attach to requirements by req_id at
view-build time. Maturity is therefore derived, never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from intentrace.models import AnchorRef, Decision, Ratification, Requirement
from intentrace.symbol import SymbolTable


@dataclass(frozen=True)
class DecisionView:
    """Requirements annotated from the decision log, plus orphans."""

    requirements: list[Requirement] = field(default_factory=list)
    orphaned: list[Decision] = field(default_factory=list)


def apply_decisions(requirements: list[Requirement], decisions: list[Decision]) -> DecisionView:
    """Attach ratifications to requirements; collect orphaned decisions.

    The earliest ratify decision for a req_id wins: the first ratification
    is when the human took responsibility. Non-ratify kinds carry no
    maturity semantics yet (later slices) but still count for orphaning:
    a decision pointing at a vanished requirement is reported either way.
    """
    ratifies: dict[str, list[Decision]] = {}
    for d in decisions:
        if d.kind == "ratify":
            ratifies.setdefault(d.req_id, []).append(d)

    known_ids = {r.req_id for r in requirements}
    annotated: list[Requirement] = []
    for req in requirements:
        matches = ratifies.get(req.req_id, [])
        if not matches:
            annotated.append(req)
            continue
        first = min(matches, key=lambda d: d.timestamp)
        annotated.append(
            req.model_copy(
                update={
                    "maturity": "active",
                    "ratification": Ratification(
                        actor=first.actor,
                        timestamp=first.timestamp,
                        settle_id=first.settle_id,
                        baseline_hashes=dict(first.baseline_hashes),
                    ),
                }
            )
        )

    orphaned = [d for d in decisions if d.req_id not in known_ids]
    return DecisionView(requirements=annotated, orphaned=orphaned)


@dataclass(frozen=True)
class Fresh:
    """The candidate's anchors match current code; safe to baseline."""

    baseline: dict[str, str]  # symbol_path -> node_hash at this instant


@dataclass(frozen=True)
class Stale:
    """Code moved since the proposal; the candidate must be re-presented."""

    fresh_anchors: list[AnchorRef]
    changed: list[str] = field(default_factory=list)  # paths with a new hash
    missing: list[str] = field(default_factory=list)  # paths that no longer resolve


def check_fresh(candidate: Requirement, symbols: SymbolTable) -> Fresh | Stale:
    """Re-validate a proposal against current code (I3).

    Compares each proposed anchor against a freshly built symbol table.
    A changed hash or a vanished symbol means the human has not seen this
    code: refuse, and carry the re-derived anchors so the caller can
    present the current candidate instead. Never fabricate a baseline for
    code the human has not seen.
    """
    baseline: dict[str, str] = {}
    fresh_anchors: list[AnchorRef] = []
    changed: list[str] = []
    missing: list[str] = []

    for anchor in candidate.anchors:
        current = symbols.by_qualified.get(anchor.symbol_path)
        if current is None:
            missing.append(anchor.symbol_path)
        elif current.node_hash != anchor.node_hash:
            changed.append(anchor.symbol_path)
            baseline[anchor.symbol_path] = current.node_hash
            fresh_anchors.append(current)
        else:
            baseline[anchor.symbol_path] = current.node_hash
            fresh_anchors.append(current)

    if changed or missing:
        return Stale(fresh_anchors=fresh_anchors, changed=changed, missing=missing)
    return Fresh(baseline=baseline)


@dataclass(frozen=True)
class AnchorVerdict:
    """One anchor judged against the ratified baseline."""

    symbol_path: str
    state: Literal["ok", "changed", "missing", "detached"]
    baseline_hash: str | None = None
    current_hash: str | None = None


@dataclass(frozen=True)
class ReqVerdict:
    """Drift status of one requirement for display."""

    status: Literal["unverified", "unconfirmed", "orphaned", "unimplemented"]
    anchors: list[AnchorVerdict] = field(default_factory=list)


def judge_requirement(req: Requirement, symbols: SymbolTable) -> ReqVerdict:
    """Judge an annotated requirement against current code.

    Sketches are always unverified. Active requirements compare each
    baseline path against the current table: a changed hash is unconfirmed,
    a vanished symbol is orphaned, a path the extractor no longer attaches
    is detached (still unconfirmed — the human must look). Missing takes
    precedence over changed. An active requirement with no anchors at all
    is unimplemented.
    """
    if req.maturity == "sketch" or req.ratification is None:
        return ReqVerdict(status="unverified")

    baseline = req.ratification.baseline_hashes
    current_paths = {a.symbol_path for a in req.anchors}
    details: list[AnchorVerdict] = []

    for path in current_paths:
        current = symbols.by_qualified.get(path)
        base = baseline.get(path)
        if current is None:
            details.append(AnchorVerdict(path, "missing", base, None))
        elif base is not None and current.node_hash != base:
            details.append(AnchorVerdict(path, "changed", base, current.node_hash))

    for path, base in baseline.items():
        if path in current_paths:
            continue
        current = symbols.by_qualified.get(path)
        if current is None:
            details.append(AnchorVerdict(path, "missing", base, None))
        elif current.node_hash != base:
            details.append(AnchorVerdict(path, "changed", base, current.node_hash))
        else:
            details.append(AnchorVerdict(path, "detached", base, current.node_hash))

    if not details and not baseline and not current_paths:
        return ReqVerdict(status="unimplemented")
    if any(d.state == "missing" for d in details):
        return ReqVerdict(status="orphaned", anchors=details)
    if details:
        return ReqVerdict(status="unconfirmed", anchors=details)
    return ReqVerdict(status="unverified")


def match_req_id(prefix: str, requirements: list[Requirement]) -> list[Requirement]:
    """Resolve a req_id prefix against extracted requirements.

    An exact match wins outright; otherwise all prefix matches are
    returned so the caller can report none-or-ambiguous honestly.
    A leading "R-" (the display form from `why`) is accepted.
    """
    prefix = prefix.removeprefix("R-")
    exact = [r for r in requirements if r.req_id == prefix]
    if exact:
        return exact
    return [r for r in requirements if r.req_id.startswith(prefix)]
