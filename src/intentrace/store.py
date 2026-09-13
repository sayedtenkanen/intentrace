"""In-memory store rebuilt from the log.

Interface plus one in-memory implementation.
Lookups: requirements by anchor symbol_path, requirement by req_id, observation by obs_id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from intentrace.log import read_observations
from intentrace.models import Observation, Requirement


class Store(Protocol):
    """Interface for requirement and observation storage."""

    def get_requirement(self, req_id: str) -> Requirement | None: ...
    def get_requirements_by_anchor(self, symbol_path: str) -> list[Requirement]: ...
    def get_observation(self, obs_id: str) -> Observation | None: ...
    def add_requirements(self, requirements: list[Requirement]) -> None: ...
    @property
    def all_requirements(self) -> list[Requirement]: ...
    @property
    def all_observations(self) -> list[Observation]: ...


class MemoryStore:
    """In-memory store rebuilt from the log."""

    def __init__(self, repo_root: Path) -> None:
        self._observations: dict[str, Observation] = {}
        self._requirements: dict[str, Requirement] = {}
        self._anchor_index: dict[str, list[str]] = {}  # symbol_path -> [req_id]
        self._load(repo_root)

    def _load(self, repo_root: Path) -> None:
        """Rebuild state from the log."""
        result = read_observations(repo_root)
        for obs in result.observations:
            self._observations[obs.obs_id] = obs

    def get_requirement(self, req_id: str) -> Requirement | None:
        return self._requirements.get(req_id)

    def get_requirements_by_anchor(self, symbol_path: str) -> list[Requirement]:
        req_ids = self._anchor_index.get(symbol_path, [])
        return [self._requirements[rid] for rid in req_ids if rid in self._requirements]

    def get_observation(self, obs_id: str) -> Observation | None:
        return self._observations.get(obs_id)

    def add_requirements(self, requirements: list[Requirement]) -> None:
        """Add requirements to the store and update indices."""
        for req in requirements:
            self._requirements[req.req_id] = req
            for anchor in req.anchors:
                self._anchor_index.setdefault(anchor.symbol_path, []).append(req.req_id)

    @property
    def all_requirements(self) -> list[Requirement]:
        return list(self._requirements.values())

    @property
    def all_observations(self) -> list[Observation]:
        return list(self._observations.values())
