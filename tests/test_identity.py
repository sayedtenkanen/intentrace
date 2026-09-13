"""Tests for content-addressed identity of observations and requirements."""

from __future__ import annotations

from intentrace.extract.fake import FakeExtractor
from intentrace.models import Observation, Requirement, Span


def test_obs_id_deterministic() -> None:
    """Same content produces the same obs_id."""
    obs1 = Observation.create(session_id="s1", turn_index=0, kind="prompt", text="hello")
    obs2 = Observation.create(session_id="s1", turn_index=0, kind="prompt", text="hello")
    assert obs1.obs_id == obs2.obs_id


def test_obs_id_differs_on_content() -> None:
    """Different content produces different obs_id."""
    obs1 = Observation.create(session_id="s1", turn_index=0, kind="prompt", text="hello")
    obs2 = Observation.create(session_id="s1", turn_index=0, kind="prompt", text="world")
    assert obs1.obs_id != obs2.obs_id


def test_req_id_deterministic() -> None:
    """Same input produces the same req_id."""
    provenance = [Span(obs_id="abc", start=0, end=5)]
    req1 = Requirement.create(
        statement="test statement",
        provenance=provenance,
        extractor_version="v1",
    )
    req2 = Requirement.create(
        statement="test statement",
        provenance=provenance,
        extractor_version="v1",
    )
    assert req1.req_id == req2.req_id


def test_req_id_differs_on_version() -> None:
    """Different extractor version produces different req_id."""
    provenance = [Span(obs_id="abc", start=0, end=5)]
    req1 = Requirement.create(
        statement="test statement",
        provenance=provenance,
        extractor_version="v1",
    )
    req2 = Requirement.create(
        statement="test statement",
        provenance=provenance,
        extractor_version="v2",
    )
    assert req1.req_id != req2.req_id


def test_two_runs_identical_requirements() -> None:
    """Deterministic: two runs over one log produce identical requirement sets."""
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The system must handle errors gracefully.",
    )
    extractor = FakeExtractor()
    reqs1 = extractor.extract([obs])
    reqs2 = extractor.extract([obs])
    assert len(reqs1) == len(reqs2)
    for r1, r2 in zip(reqs1, reqs2, strict=True):
        assert r1.req_id == r2.req_id
        assert r1.statement == r2.statement
