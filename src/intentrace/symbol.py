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

    def resolve_qualified(self, class_name: str, method_name: str) -> Resolution:
        """Resolve a Class.method reference from a sentence.

        Matches qualified paths ending in ::Class::method, so naming the
        class resolves deterministically even when the bare method name is
        ambiguous. The leading :: in the match keeps partial class names
        from matching (AA never matches A). Candidates are sorted by path
        so ambiguity reports are deterministic.
        """
        suffix = f"::{class_name}::{method_name}"
        candidates = sorted(
            (a for a in self.by_qualified.values() if a.symbol_path.endswith(suffix)),
            key=lambda a: a.symbol_path,
        )
        if len(candidates) == 0:
            return NotFound()
        if len(candidates) == 1:
            return Resolved(anchor=candidates[0])
        return Ambiguous(candidates=tuple(candidates))
