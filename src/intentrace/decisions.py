"""Decision replay and freshness for the settle loop.

Slices 1-2 derived every requirement from fresh extraction and attached
ratifications to it by req_id. Slice 4 makes the ratify decision primary: it
carries a snapshot (statement, provenance, extractor version), so a version
bump can no longer hide what a human ratified, and fresh extraction supplies
only sketches.

Decisions persist in the append-only log. The view is built on every read:
ratified snapshots, plus re-derived sketches, plus the sketches suppressed or
attached to a snapshot. Maturity is derived, never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from intentrace.models import (
    AnchorRef,
    Decision,
    Derivation,
    Ratification,
    Requirement,
    _normalize_for_hash,
)
from intentrace.symbol import SymbolTable

KNOWN_DECISION_KINDS = frozenset({"ratify", "waive", "demote", "archive", "migrate", "attest"})


@dataclass(frozen=True)
class Suppressed:
    """A sketch not proposed because a ratified snapshot already covers it."""

    sketch: Requirement
    snapshot_req_id: str
    reason: Literal["same span", "same statement"]


@dataclass(frozen=True)
class Attached:
    """A sketch shown attached to a snapshot instead of being dropped."""

    sketch: Requirement
    snapshot_req_id: str
    reason: Literal["same words, different code", "possible successor"]


@dataclass(frozen=True)
class DecisionView:
    """Requirements annotated from the decision log, plus what was set aside."""

    requirements: list[Requirement] = field(default_factory=list)
    orphaned: list[Decision] = field(default_factory=list)
    suppressed: list[Suppressed] = field(default_factory=list)
    attached: list[Attached] = field(default_factory=list)
    unknown: list[Decision] = field(default_factory=list)


def _resolve_paths(paths: list[str], symbols: SymbolTable | None) -> list[AnchorRef]:
    if symbols is None:
        return []
    return [symbols.by_qualified[p] for p in paths if p in symbols.by_qualified]


def _spans_overlap(sketch: Requirement, active: Requirement) -> bool:
    return any(
        p.obs_id == q.obs_id and p.start < q.end and q.start < p.end
        for p in sketch.provenance
        for q in active.provenance
    )


def _same_span(sketch: Requirement, active: Requirement) -> bool:
    return any(
        p.obs_id == q.obs_id and p.start == q.start and p.end == q.end
        for p in sketch.provenance
        for q in active.provenance
    )


def apply_decisions(
    requirements: list[Requirement],
    decisions: list[Decision],
    symbols: SymbolTable | None = None,
) -> DecisionView:
    """Build the view: ratified snapshots, re-derived sketches, and set-asides.

    A ratify decision is primary only if it carries a snapshot. Decisions
    written before slice 4 have none; they are reported orphaned with the
    reason, never silently promoted. A sketch is suppressed when a snapshot
    already covers the same span or the same normalised statement. A sketch
    proposing an anchor the snapshot lacks is attached as a late anchor; a
    partial span overlap is attached as a possible successor.
    """
    ratifies: dict[str, list[Decision]] = {}
    attests: dict[str, list[Decision]] = {}
    unknown: list[Decision] = []

    for d in decisions:
        if d.kind == "ratify":
            ratifies.setdefault(d.req_id, []).append(d)
        elif d.kind == "attest":
            attests.setdefault(d.req_id, []).append(d)
        elif d.kind not in KNOWN_DECISION_KINDS:
            unknown.append(d)

    active: list[Requirement] = []
    legacy_orphans: list[Decision] = []

    for req_id, records in ratifies.items():
        snapshot_decision = next((d for d in records if d.snapshot is not None), None)
        if snapshot_decision is None:
            legacy_orphans.extend(records)
            continue
        assert snapshot_decision.snapshot is not None

        baseline = dict(snapshot_decision.baseline_hashes)
        name_baseline = dict(snapshot_decision.baseline_name_hashes)
        last_attest: Decision | None = None
        for attest in attests.get(req_id, []):
            if attest.baseline_hashes:
                baseline = dict(attest.baseline_hashes)
            if attest.baseline_name_hashes:
                name_baseline = dict(attest.baseline_name_hashes)
            last_attest = attest

        active.append(
            Requirement(
                req_id=req_id,
                statement=snapshot_decision.snapshot.statement,
                origin="declared",
                maturity="active",
                provenance=snapshot_decision.snapshot.provenance,
                derivation=Derivation(
                    extractor_version=snapshot_decision.snapshot.extractor_version,
                    timestamp=snapshot_decision.timestamp,
                ),
                anchors=_resolve_paths(list(baseline), symbols),
                ratification=Ratification(
                    actor=snapshot_decision.actor,
                    timestamp=snapshot_decision.timestamp,
                    settle_id=snapshot_decision.settle_id,
                    baseline_hashes=baseline,
                    baseline_name_hashes=name_baseline,
                    attested_at=last_attest.timestamp if last_attest else None,
                    attest_actor=last_attest.actor if last_attest else None,
                    attest_reason=last_attest.reason if last_attest else None,
                ),
            )
        )

    active_ids = {r.req_id for r in active}
    orphaned = list(legacy_orphans)
    for d in decisions:
        if d.req_id in active_ids:
            continue
        if d.kind in KNOWN_DECISION_KINDS and d.kind != "ratify":
            orphaned.append(d)

    suppressed: list[Suppressed] = []
    attached: list[Attached] = []
    sketches: list[Requirement] = []

    for sketch in requirements:
        span_match = next((a for a in active if _same_span(sketch, a)), None)
        hash_match = next(
            (
                a
                for a in active
                if _normalize_for_hash(a.statement) == _normalize_for_hash(sketch.statement)
            ),
            None,
        )
        match = span_match or hash_match
        if match is None:
            if any(_spans_overlap(sketch, a) for a in active):
                target = next(a for a in active if _spans_overlap(sketch, a))
                attached.append(Attached(sketch, target.req_id, "possible successor"))
            sketches.append(sketch)
            continue

        recorded = {a.symbol_path for a in match.anchors}
        extras = [a for a in sketch.anchors if a.symbol_path not in recorded]
        if extras:
            for anchor in extras:
                if anchor.symbol_path not in {a.symbol_path for a in match.anchors}:
                    match.anchors.append(anchor)
            attached.append(Attached(sketch, match.req_id, "same words, different code"))
        else:
            suppressed.append(
                Suppressed(
                    sketch,
                    match.req_id,
                    "same span" if span_match is not None else "same statement",
                )
            )

    return DecisionView(
        requirements=[*active, *sketches],
        orphaned=orphaned,
        suppressed=suppressed,
        attached=attached,
        unknown=unknown,
    )


@dataclass(frozen=True)
class Fresh:
    """The candidate's anchors match current code; safe to baseline."""

    baseline: dict[str, str]  # symbol_path -> node_hash at this instant
    baseline_name_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Stale:
    """Code moved since the proposal; the candidate must be re-presented."""

    fresh_anchors: list[AnchorRef]
    changed: list[str] = field(default_factory=list)  # paths with a new hash
    missing: list[str] = field(default_factory=list)  # paths that no longer resolve
    detached: list[str] = field(default_factory=list)  # paths the extractor dropped
    attached: list[str] = field(default_factory=list)  # paths the extractor added


def check_fresh(
    candidate: Requirement,
    symbols: SymbolTable,
    fresh_requirements: list[Requirement],
) -> Fresh | Stale:
    """Re-validate a proposal against a fresh extraction (I3).

    Compares the proposed anchor set against what the current extractor
    attaches: a changed hash, a vanished symbol, a dropped attachment
    (e.g. newly ambiguous), or a newly attached anchor all mean the human
    has not seen this candidate. Refuse, and carry the re-derived anchors
    so the caller can present the current candidate instead. Never
    fabricate a baseline for code the human has not seen.
    """
    fresh = next((r for r in fresh_requirements if r.req_id == candidate.req_id), None)
    if fresh is None:
        # The extractor no longer produces this requirement at all.
        return Stale(
            fresh_anchors=[],
            detached=[a.symbol_path for a in candidate.anchors],
        )

    fresh_by_path = {a.symbol_path: a for a in fresh.anchors}
    cand_by_path = {a.symbol_path: a for a in candidate.anchors}
    baseline: dict[str, str] = {}
    name_baseline: dict[str, str] = {}
    changed: list[str] = []
    missing: list[str] = []
    detached: list[str] = []
    attached: list[str] = []

    for path, anchor in cand_by_path.items():
        current = fresh_by_path.get(path)
        if current is None:
            if symbols.by_qualified.get(path) is None:
                missing.append(path)
            else:
                detached.append(path)
        elif current.node_hash != anchor.node_hash:
            changed.append(path)
            baseline[path] = current.node_hash
            name_baseline[path] = current.name_hash
        else:
            baseline[path] = current.node_hash
            name_baseline[path] = current.name_hash

    for path, anchor in fresh_by_path.items():
        if path not in cand_by_path:
            attached.append(path)
            baseline[path] = anchor.node_hash
            name_baseline[path] = anchor.name_hash

    if changed or missing or detached or attached:
        return Stale(
            fresh_anchors=list(fresh.anchors),
            changed=changed,
            missing=missing,
            detached=detached,
            attached=attached,
        )
    return Fresh(baseline=baseline, baseline_name_hashes=name_baseline)


@dataclass(frozen=True)
class AnchorVerdict:
    """One anchor judged against the ratified baseline."""

    symbol_path: str
    state: Literal["changed", "missing", "pending"]
    baseline_hash: str | None = None
    current_hash: str | None = None


@dataclass(frozen=True)
class ReqVerdict:
    """Drift status of one requirement for display."""

    status: Literal["unverified", "unconfirmed", "orphaned", "unimplemented"]
    anchors: list[AnchorVerdict] = field(default_factory=list)


def judge_requirement(req: Requirement, symbols: SymbolTable) -> ReqVerdict:
    """Judge an annotated requirement against current code.

    Sketches are always unverified. Active anchors are the recorded set:
    a changed hash is unconfirmed, a vanished symbol is orphaned, and an
    anchor with no baseline is pending (unconfirmed) until a human attests
    it. Missing takes precedence over changed. An active requirement with no
    anchors and no baseline is unimplemented.
    """
    if req.maturity == "sketch" or req.ratification is None:
        return ReqVerdict(status="unverified")

    baseline = req.ratification.baseline_hashes
    current_paths = {a.symbol_path for a in req.anchors}
    details: list[AnchorVerdict] = []
    seen: set[str] = set()

    for path in sorted(current_paths):
        current = symbols.by_qualified.get(path)
        base = baseline.get(path)
        if current is None:
            details.append(AnchorVerdict(path, "missing", base, None))
        elif base is None:
            details.append(AnchorVerdict(path, "pending", None, current.node_hash))
        elif current.node_hash != base:
            details.append(AnchorVerdict(path, "changed", base, current.node_hash))
        else:
            continue
        seen.add(path)

    for path, base in sorted(baseline.items()):
        if path in current_paths or path in seen:
            continue
        current = symbols.by_qualified.get(path)
        if current is None:
            details.append(AnchorVerdict(path, "missing", base, None))
        elif current.node_hash != base:
            details.append(AnchorVerdict(path, "changed", base, current.node_hash))

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
