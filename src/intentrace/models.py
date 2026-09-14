"""Core data models for intentrace.

A strict subset of docs/SPEC.md §6 for Slice 1.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, field_validator


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


class Requirement(BaseModel):
    """A statement of intended behaviour, with provenance and anchors."""

    req_id: str
    statement: str
    origin: Literal["declared"]
    maturity: Literal["sketch"]
    provenance: list[Span]
    derivation: Derivation
    anchors: list[AnchorRef]
    evidence: list[EvidenceRef] = []

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
