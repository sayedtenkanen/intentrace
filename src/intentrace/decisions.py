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
    ATTEST_REASONS,
    AnchorRef,
    Attestation,
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
    """A sketch shown beside a snapshot it resembles; it stays in the queue."""

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


def _resolve_paths(paths: list[str], symbols: SymbolTable) -> list[AnchorRef]:
    return [symbols.by_qualified[p] for p in paths if p in symbols.by_qualified]


def _is_valid_attestation(decision: Decision) -> bool:
    """Defensive replay check, mirroring the Decision model validator.

    The model rejects malformed attestations at the boundary; this keeps a
    record that bypassed validation (or was written by a future version)
    from replacing a trusted baseline.
    """
    if decision.reason not in ATTEST_REASONS:
        return False
    return decision.reason != "drift-accepted" or bool(
        decision.rationale and decision.rationale.strip()
    )


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


def replay_baselines(
    decisions: list[Decision], snapshot_decision: Decision
) -> tuple[dict[str, str], dict[str, str], list[Attestation]]:
    """Replay the valid attestations appended after one snapshot-bearing ratify.

    Returns the baseline in force now, its name-insensitive companion, and
    every attestation that replaced it, in append order. An attestation
    belonging to a superseded ratification, or to another requirement, is
    ignored. Position in the log is the only order.
    """
    baseline = dict(snapshot_decision.baseline_hashes)
    name_baseline = dict(snapshot_decision.baseline_name_hashes)
    attestations: list[Attestation] = []
    snapshot_index = next(
        index for index, decision in enumerate(decisions) if decision is snapshot_decision
    )
    for attest in decisions[snapshot_index + 1 :]:
        if attest.kind != "attest" or attest.req_id != snapshot_decision.req_id:
            continue
        # An attestation always replaces the baseline, including with an
        # empty one: that is how a deleted anchor is acknowledged. A
        # malformed attestation never replaces a trusted baseline.
        if not _is_valid_attestation(attest):
            continue
        baseline = dict(attest.baseline_hashes)
        name_baseline = dict(attest.baseline_name_hashes)
        attestations.append(
            Attestation(
                timestamp=attest.timestamp,
                actor=attest.actor,
                reason=attest.reason or "",
                rationale=attest.rationale,
            )
        )
    return baseline, name_baseline, attestations


def apply_decisions(
    requirements: list[Requirement],
    decisions: list[Decision],
    symbols: SymbolTable,
) -> DecisionView:
    """Build the view: ratified snapshots, re-derived sketches, and set-asides.

    The symbol table is required: replay must resolve recorded baselines to
    anchors. Callers that omit it would silently produce active
    requirements with no anchors, which is indistinguishable from code that
    vanished. A ratify decision is primary only if it carries a snapshot.
    Decisions written before slice 4 have none; they are reported orphaned
    with the reason, never silently promoted. A sketch is suppressed when a
    snapshot already covers the same span or the same normalised statement.
    A sketch from the same span that proposes an anchor the snapshot lacks is
    a late anchor on that requirement: the anchor is shown as pending and is
    not in the baseline until attested. A sketch from a different observation
    with the same words is shown beside it as "same words, different code"
    and stays in the queue; the snapshot's anchor set is not changed. Coverage
    is decided against the union of every same-statement requirement's
    effective baseline, never against a pending anchor. A partial span overlap
    is shown as a possible successor and also stays in the queue.

    Decisions are replayed in the order they were appended. A timestamp is
    display data and never decides which act came first; this holds even when
    the clock stepped backwards between two appends.
    """
    ratifies: dict[str, list[Decision]] = {}
    unknown: list[Decision] = []

    for d in decisions:
        if d.kind == "ratify":
            ratifies.setdefault(d.req_id, []).append(d)
        elif d.kind not in KNOWN_DECISION_KINDS:
            unknown.append(d)

    active: list[Requirement] = []
    legacy_orphans: list[Decision] = []

    for req_id, records in ratifies.items():
        # The most recent snapshot-bearing ratify wins. A re-ratification is
        # a later human act; selecting the first would silently keep a stale
        # baseline.
        snapshot_decision = next((d for d in reversed(records) if d.snapshot is not None), None)
        if snapshot_decision is None:
            legacy_orphans.extend(records)
            continue
        assert snapshot_decision.snapshot is not None

        # Only attestations appended after this ratification may replace its
        # baseline. An earlier attestation belongs to a superseded ratification.
        baseline, name_baseline, attestations = replay_baselines(decisions, snapshot_decision)
        last_attest = attestations[-1] if attestations else None

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
                    attestations=attestations,
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
        statement = _normalize_for_hash(sketch.statement)
        statement_matches = [a for a in active if _normalize_for_hash(a.statement) == statement]
        if span_match is not None and span_match not in statement_matches:
            # A same-span sketch is necessarily the same utterance; include it
            # even if a defensive caller supplied odd provenance.
            statement_matches.append(span_match)
        match = span_match or (statement_matches[0] if statement_matches else None)
        if match is None:
            if any(_spans_overlap(sketch, a) for a in active):
                target = next(a for a in active if _spans_overlap(sketch, a))
                attached.append(Attached(sketch, target.req_id, "possible successor"))
            sketches.append(sketch)
            continue

        # Coverage is always the effective baseline: attestations included,
        # pending anchors excluded. A pending anchor is display state, not a
        # human decision, and must not hide a later proposal.
        covered: set[str] = set()
        for existing in statement_matches:
            assert existing.ratification is not None
            covered.update(existing.ratification.baseline_hashes)
        proposed = {a.symbol_path for a in sketch.anchors}
        extras = [a for a in sketch.anchors if a.symbol_path not in covered]

        if span_match is not None:
            # The same utterance re-derived with a new anchor: this is a late
            # anchor on that requirement. It is visible as pending and becomes
            # part of the baseline only when a human attests.
            for anchor in extras:
                if anchor.symbol_path not in {a.symbol_path for a in span_match.anchors}:
                    span_match.anchors.append(anchor)
            if not extras:
                suppressed.append(Suppressed(sketch, span_match.req_id, "same span"))
            continue

        if proposed <= covered:
            # Every proposed anchor is already in force on some same-statement
            # requirement. Point the suppression at a requirement that covers
            # the whole proposal when one exists; otherwise a match covering
            # part of it is still an honest place to look.
            covering = next(
                (
                    existing
                    for existing in statement_matches
                    if existing.ratification is not None
                    and proposed <= set(existing.ratification.baseline_hashes)
                ),
                match,
            )
            suppressed.append(Suppressed(sketch, covering.req_id, "same statement"))
            continue

        # Same words, different observation and code: show the sketch beside
        # the requirement it resembles and leave it in the queue. It is not
        # suppressed, and it does not silently become an anchor of the
        # existing requirement. A human decides whether it is a separate
        # obligation.
        attached.append(Attached(sketch, match.req_id, "same words, different code"))
        sketches.append(sketch)

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
