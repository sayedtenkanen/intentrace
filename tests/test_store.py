"""Tests for the in-memory store."""

from __future__ import annotations

from pathlib import Path

from intentrace.log import append_observation
from intentrace.models import AnchorRef, Observation, Requirement, Span
from intentrace.store import MemoryStore


def _make_obs(text: str = "test") -> Observation:
    return Observation.create(session_id="s1", turn_index=0, kind="prompt", text=text)


def test_store_loads_observations(repo_root: Path) -> None:
    """Store rebuilds observations from the log."""
    obs = _make_obs("hello")
    append_observation(repo_root, obs)

    store = MemoryStore(repo_root)
    assert len(store.all_observations) == 1
    assert store.all_observations[0].text == "hello"


def test_store_get_observation(repo_root: Path) -> None:
    """Store can look up an observation by obs_id."""
    obs = _make_obs("hello")
    append_observation(repo_root, obs)

    store = MemoryStore(repo_root)
    found = store.get_observation(obs.obs_id)
    assert found is not None
    assert found.text == "hello"


def test_store_add_requirements(repo_root: Path) -> None:
    """Store can add and retrieve requirements."""
    store = MemoryStore(repo_root)
    req = Requirement.create(
        statement="test requirement",
        provenance=[Span(obs_id="abc", start=0, end=5)],
        extractor_version="v1",
    )
    store.add_requirements([req])

    found = store.get_requirement(req.req_id)
    assert found is not None
    assert found.statement == "test requirement"


def test_store_requirements_by_anchor(repo_root: Path) -> None:
    """Store can look up requirements by anchor symbol_path."""
    store = MemoryStore(repo_root)
    req = Requirement.create(
        statement="retry must work",
        provenance=[Span(obs_id="abc", start=0, end=5)],
        extractor_version="v1",
        anchors=[
            AnchorRef(
                lang="python",
                file="retry.py",
                symbol_path="retry.py::RetryPolicy::attempt",
                node_kind="function_definition",
                node_hash="abc123",
            )
        ],
    )
    store.add_requirements([req])

    found = store.get_requirements_by_anchor("retry.py::RetryPolicy::attempt")
    assert len(found) == 1
    assert found[0].req_id == req.req_id


def test_store_empty(repo_root: Path) -> None:
    """Empty store returns empty results."""
    store = MemoryStore(repo_root)
    assert store.all_observations == []
    assert store.all_requirements == []
    assert store.get_observation("nonexistent") is None
    assert store.get_requirement("nonexistent") is None
