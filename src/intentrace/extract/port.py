"""Extractor port — the interface for requirement extraction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from intentrace.models import AnchorRef, Observation, Requirement


class ExtractorPort(Protocol):
    """Protocol for requirement extractors."""

    version: str

    def extract(
        self,
        observations: Iterable[Observation],
        symbols: dict[str, AnchorRef] | None = None,
    ) -> list[Requirement]: ...
