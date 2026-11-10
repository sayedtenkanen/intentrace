"""Core data models for intentrace.

A strict subset of docs/SPEC.md §6 for slices 1 and 2.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

ATTEST_REASONS = frozenset({"late-anchor", "rename", "move", "drift-accepted"})


def content_hash(*parts: str) -> str:
    """SHA-256 hex digest of the concatenated parts."""
    return hashlib.sha256("".join(parts).encode()).hexdigest()


def canonical_json(*parts: str) -> str:
    """Canonical JSON encoding for identity hashing.

    JSON-encodes the parts as a list with sorted keys and minimal separators.
    """
    import json

    return json.dumps(list(parts), sort_keys=True, separators=(",", ":"))


def _normalize_for_hash(statement: str) -> str:
    """Normalize a statement for identity hashing only.

    Applies collapse whitespace, lowercase, and strip trailing punctuation.
    The stored statement is NOT normalized this way — only the hash input is.
    """
    normalized = " ".join(statement.split())
    normalized = normalized.lower()
    normalized = normalized.rstrip(".!?")
    return normalized


class Span(BaseModel):
    """A char-offset range within an observation."""

    obs_id: str
    start: int  # inclusive
    end: int  # exclusive

    @field_validator("start")
    @classmethod
    def start_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("start must be non-negative")
        return v

    @field_validator("end")
    @classmethod
    def end_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("end must be positive")
        return v

    @field_validator("end")
    @classmethod
    def end_after_start(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Enforce end > start."""
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError(f"end ({v}) must be greater than start ({start})")
        return v


class Derivation(BaseModel):
    """How a requirement was produced."""

    extractor_version: str
    timestamp: datetime


class AnchorRef(BaseModel):
    """A link from a requirement to a code location."""

    lang: Literal["python"]
    file: str  # repo-relative
    symbol_path: str  # e.g. "retry.py::RetryPolicy::attempt"
    node_kind: str  # tree-sitter node type
    node_hash: str  # hash of the NORMALIZED subtree
    name_hash: str = ""  # normalized subtree with its own name erased (rename matching)


class EvidenceRef(BaseModel):
    """Reference to evidence (test id, check id, or attestation)."""

    kind: Literal["test", "check", "attestation"]
    ref: str
    timestamp: datetime | None = None


class Observation(BaseModel):
    """An immutable captured event."""

    obs_id: str
    session_id: str
    turn_index: int
    kind: Literal["prompt", "tool_result", "diff", "commit"]
    text: str
    timestamp: datetime
    source: str

    @classmethod
    def create(
        cls,
        session_id: str,
        turn_index: int,
        kind: Literal["prompt", "tool_result", "diff", "commit"],
        text: str,
        timestamp: datetime | None = None,
        source: str = "ingest",
    ) -> Observation:
        """Create an observation with a content-addressed id."""
        ts = timestamp or datetime.now(UTC)
        raw = canonical_json(kind, text, session_id, str(turn_index))
        obs_id = content_hash(raw)
        return cls(
            obs_id=obs_id,
            session_id=session_id,
            turn_index=turn_index,
            kind=kind,
            text=text,
            timestamp=ts,
            source=source,
        )


class RequirementSnapshot(BaseModel):
    """What a ratify decision recorded, independent of any later extraction.

    This is primary storage for a ratified requirement: the statement,
    the provenance spans that justify it, and the extractor version that
    produced it. Fresh extraction can never hide it.
    """

    statement: str
    provenance: list[Span]
    extractor_version: str

    @field_validator("provenance")
    @classmethod
    def provenance_non_empty(cls, v: list[Span]) -> list[Span]:
        """I9 holds for the snapshot too: a ratification always cites a span."""
        if not v:
            raise ValueError("provenance must be non-empty (I9)")
        return v


class Attestation(BaseModel):
    """One recorded attestation, in the order it was appended.

    Kept as history rather than collapsed to the last line: `why` shows the
    count and the last act by default, and every earlier rationale remains
    reachable from the command line.
    """

    timestamp: datetime
    actor: str
    reason: str
    rationale: str | None = None


class Ratification(BaseModel):
    """The recorded consequence of a human taking responsibility.

    Attached to a requirement at view-build time from the decision log;
    never stored on the requirement itself. baseline_hashes maps each
    ratified symbol_path to the node_hash as it was at ratification (I3).
    baseline_name_hashes carries the rename-insensitive hash for the same
    paths, so a later rename can be proposed without re-deriving the old
    body.

    attestations holds every valid attestation appended after this
    ratification, in append order. The attested_at/attest_actor/
    attest_reason fields repeat the last one for existing callers.
    """

    actor: str
    timestamp: datetime
    settle_id: str = ""
    baseline_hashes: dict[str, str] = {}
    baseline_name_hashes: dict[str, str] = {}
    attested_at: datetime | None = None
    attest_actor: str | None = None
    attest_reason: str | None = None
    attestations: list[Attestation] = []


class Requirement(BaseModel):
    """A statement of intended behaviour, with provenance and anchors."""

    req_id: str
    statement: str
    origin: Literal["declared"]
    maturity: Literal["sketch", "active"]
    provenance: list[Span]
    derivation: Derivation
    anchors: list[AnchorRef]
    evidence: list[EvidenceRef] = []
    ratification: Ratification | None = None

    @field_validator("provenance")
    @classmethod
    def provenance_non_empty(cls, v: list[Span]) -> list[Span]:
        """I9: every requirement cites at least one observation span."""
        if not v:
            raise ValueError("provenance must be non-empty (I9)")
        return v

    @classmethod
    def create(
        cls,
        statement: str,
        provenance: list[Span],
        extractor_version: str,
        anchors: list[AnchorRef] | None = None,
    ) -> Requirement:
        """Create a requirement with a content-addressed id.

        The hash input is normalized (collapse whitespace, lowercase, strip
        trailing punctuation). The stored statement retains original case and
        punctuation, with only whitespace collapsed for provenance span validity.
        """
        stored_stmt = " ".join(statement.split())
        hash_input = _normalize_for_hash(statement)
        prov_parts: list[str] = []
        for p in provenance:
            prov_parts.append(f"{p.obs_id}:{p.start}:{p.end}")
        raw = canonical_json(hash_input, *prov_parts, extractor_version)
        req_id = content_hash(raw)
        return cls(
            req_id=req_id,
            statement=stored_stmt,
            origin="declared",
            maturity="sketch",
            provenance=provenance,
            derivation=Derivation(
                extractor_version=extractor_version,
                timestamp=datetime.now(UTC),
            ),
            anchors=anchors or [],
        )


class Decision(BaseModel):
    """An immutable recorded human act, appended to the same JSONL log.

    kind is a plain string so that a log written by a newer version is read,
    not treated as corruption. Known kinds are ratify, waive, demote,
    archive, migrate and attest; unknown ones are inert and reported.

    A ratify decision carries the RequirementSnapshot it made primary. An
    attest decision carries the full replacement baseline and the reason the
    tool observed (late-anchor, rename, move, drift-accepted).
    """

    dec_id: str
    kind: str
    req_id: str
    actor: str
    timestamp: datetime
    settle_id: str = ""
    rationale: str | None = None
    baseline_hashes: dict[str, str] = {}
    baseline_name_hashes: dict[str, str] = {}
    reason: str | None = None
    snapshot: RequirementSnapshot | None = None

    @field_validator("kind")
    @classmethod
    def kind_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("kind must be non-empty")
        return v

    @model_validator(mode="after")
    def attest_fields_are_valid(self) -> Decision:
        """An attestation must carry a reason the tool can assign.

        Replay treats an attestation as a full baseline replacement, so an
        unrecognised reason or a drift acceptance with no rationale would
        let a bad record make drift look acknowledged. Reject it at the
        boundary, which also makes a malformed log line corruption rather
        than a silent replacement.
        """
        if self.kind != "attest":
            return self
        if self.reason not in ATTEST_REASONS:
            raise ValueError(
                f"attest reason must be one of {sorted(ATTEST_REASONS)}, got {self.reason!r}"
            )
        if self.reason == "drift-accepted" and not (self.rationale and self.rationale.strip()):
            raise ValueError("drift-accepted requires a non-empty rationale")
        return self

    @classmethod
    def create(
        cls,
        kind: str,
        req_id: str,
        actor: str,
        settle_id: str = "",
        rationale: str | None = None,
        baseline_hashes: dict[str, str] | None = None,
        baseline_name_hashes: dict[str, str] | None = None,
        reason: str | None = None,
        snapshot: RequirementSnapshot | None = None,
        timestamp: datetime | None = None,
    ) -> Decision:
        """Create a decision with a content-addressed id.

        The timestamp is recorded but not hashed, mirroring
        Observation.create: the same logical decision reproduces the same id.
        """
        ts = timestamp or datetime.now(UTC)
        baselines = dict(baseline_hashes) if baseline_hashes else {}
        name_baselines = dict(baseline_name_hashes) if baseline_name_hashes else {}
        baseline_part = canonical_json(*[f"{k}={v}" for k, v in sorted(baselines.items())])
        name_part = canonical_json(*[f"{k}={v}" for k, v in sorted(name_baselines.items())])
        snapshot_part = ""
        if snapshot is not None:
            prov = [f"{p.obs_id}:{p.start}:{p.end}" for p in snapshot.provenance]
            snapshot_part = canonical_json(snapshot.statement, *prov, snapshot.extractor_version)
        raw = canonical_json(
            kind,
            req_id,
            actor,
            settle_id,
            rationale or "",
            reason or "",
            baseline_part,
            name_part,
            snapshot_part,
        )
        dec_id = content_hash(raw)
        return cls(
            dec_id=dec_id,
            kind=kind,
            req_id=req_id,
            actor=actor,
            timestamp=ts,
            settle_id=settle_id,
            rationale=rationale,
            baseline_hashes=baselines,
            baseline_name_hashes=name_baselines,
            reason=reason,
            snapshot=snapshot,
        )
