"""Extractor port — the interface for requirement extraction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from intentrace.models import Observation, Requirement

if TYPE_CHECKING:
    from intentrace.symbol import SymbolTable


class ExtractorPort(Protocol):
    """Protocol for requirement extractors."""

    version: str

    def extract(
        self,
        observations: Iterable[Observation],
        symbols: SymbolTable | None = None,
    ) -> list[Requirement]: ...
