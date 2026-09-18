"""Extractor port — the interface for requirement extraction."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from intentrace.models import Observation, Requirement

if TYPE_CHECKING:
    from intentrace.symbol import SymbolTable


@dataclass
class ExtractionResult:
    """Result of requirement extraction.

    Carries both the requirements and any ambiguous symbols encountered
    during resolution, so that the caller can report ambiguity to the human.
    """

    requirements: list[Requirement] = field(default_factory=list)
    ambiguous_symbols: dict[str, list[str]] = field(default_factory=dict)


class ExtractorPort(Protocol):
    """Protocol for requirement extractors."""

    version: str

    def extract(
        self,
        observations: Iterable[Observation],
        symbols: SymbolTable | None = None,
    ) -> ExtractionResult: ...
