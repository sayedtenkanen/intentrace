"""Symbol table for anchor resolution.

Keyed by qualified path for determinism. Bare-name lookup from a sentence
returns all candidates; ambiguity is reported, not resolved by guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from intentrace.models import AnchorRef


@dataclass(frozen=True)
class Resolved:
    """Exactly one candidate found — definitive resolution."""

    kind: Literal["resolved"] = field(default="resolved", init=False)
    anchor: AnchorRef = field(default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class Ambiguous:
    """Multiple candidates found — the caller must not guess.

    The `candidates` list carries all options so that the ambiguity
    can be reported with their qualified paths.
    """

    kind: Literal["ambiguous"] = field(default="ambiguous", init=False)
    candidates: tuple[AnchorRef, ...] = ()


@dataclass(frozen=True)
class NotFound:
    """No candidate found."""

    kind: Literal["not_found"] = field(default="not_found", init=False)


Resolution = Resolved | Ambiguous | NotFound


@dataclass
class SymbolTable:
    """Index of symbols found in the repository.

    Primary index: qualified path -> AnchorRef (deterministic, unique).
    Secondary index: bare name -> list[AnchorRef] (for sentence resolution).
    """

    by_qualified: dict[str, AnchorRef] = field(default_factory=dict)
    by_bare: dict[str, list[AnchorRef]] = field(default_factory=dict)

    def resolve(self, name: str) -> Resolution:
        """Resolve a bare symbol name.

        Returns Resolved if exactly one candidate exists.
        Returns Ambiguous if multiple candidates exist (with all candidates).
        Returns NotFound if no candidate exists.
        """
        candidates = self.by_bare.get(name, [])
        if len(candidates) == 0:
            return NotFound()
        if len(candidates) == 1:
            return Resolved(anchor=candidates[0])
        return Ambiguous(candidates=tuple(candidates))

    def resolve_all(self, name: str) -> list[AnchorRef]:
        """Return all candidates for a bare name (for ambiguity reporting)."""
        return self.by_bare.get(name, [])
