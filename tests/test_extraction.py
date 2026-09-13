"""Tests for the FakeExtractor."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intentrace.extract.fake import FakeExtractor
from intentrace.models import Observation, Requirement


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


def test_deterministic_output(extractor: FakeExtractor) -> None:
    """Two runs over one log produce identical requirement sets."""
    obs = Observation.create(
        session_id="s1", turn_index=0, kind="prompt", text="The system must handle errors."
    )
    reqs1 = extractor.extract([obs])
    reqs2 = extractor.extract([obs])
    assert [r.req_id for r in reqs1] == [r.req_id for r in reqs2]


def test_provenance_span_exact(extractor: FakeExtractor) -> None:
    """Provenance spans, sliced out of source text, exactly reproduce the statement."""
    text = "The system must handle errors gracefully."
    obs = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=text)
    reqs = extractor.extract([obs])
    assert len(reqs) == 1

    req = reqs[0]
    assert len(req.provenance) == 1
    span = req.provenance[0]
    assert span.obs_id == obs.obs_id

    # The span should slice out the original text exactly
    sliced = text[span.start : span.end]
    assert sliced == req.statement


def test_no_normative_marker_no_candidates(extractor: FakeExtractor) -> None:
    """A prompt with no normative marker produces no candidates."""
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="Please add a retry mechanism to the error handler.",
    )
    reqs = extractor.extract([obs])
    assert len(reqs) == 0


def test_only_prompts(extractor: FakeExtractor) -> None:
    """Only prompt observations are considered."""
    obs = Observation.create(
        session_id="s1", turn_index=0, kind="tool_result", text="The system must handle errors."
    )
    reqs = extractor.extract([obs])
    assert len(reqs) == 0


def test_multiple_sentences(extractor: FakeExtractor) -> None:
    """Multiple normative sentences produce multiple requirements."""
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="Errors must be logged. The system should never crash silently.",
    )
    reqs = extractor.extract([obs])
    assert len(reqs) == 2


def test_i9_empty_provenance_raises() -> None:
    """I9: constructing a Requirement with empty provenance raises."""
    with pytest.raises(ValidationError):
        Requirement(
            req_id="test",
            statement="test",
            origin="declared",
            maturity="sketch",
            provenance=[],
            derivation={"extractor_version": "v1", "timestamp": "2026-01-01T00:00:00Z"},
            anchors=[],
        )


def test_span_end_after_start() -> None:
    """Span with end <= start raises ValidationError."""
    from intentrace.models import Span

    with pytest.raises(ValidationError):
        Span(obs_id="abc", start=10, end=5)

    with pytest.raises(ValidationError):
        Span(obs_id="abc", start=5, end=5)

    # Valid span should work
    span = Span(obs_id="abc", start=0, end=10)
    assert span.start == 0
    assert span.end == 10


def test_provenance_corresponds_to_statement() -> None:
    """For every requirement, observation.text[span] whitespace-normalized equals the statement."""
    import json
    from pathlib import Path

    fixture_dir = Path(__file__).parent / "fixtures" / "sample_project"
    obs_file = fixture_dir / "observations.jsonl"

    observations: list[Observation] = []
    with open(obs_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            observations.append(Observation.model_validate(data))

    extractor = FakeExtractor()
    requirements = extractor.extract(observations)

    # Build observation lookup
    obs_map = {obs.obs_id: obs for obs in observations}

    for req in requirements:
        assert len(req.provenance) > 0, f"requirement has no provenance: {req.statement}"
        span = req.provenance[0]
        obs = obs_map.get(span.obs_id)
        assert obs is not None, f"provenance references unknown observation: {span.obs_id}"
        sliced = obs.text[span.start : span.end]
        assert " ".join(sliced.split()) == req.statement, (
            f"provenance span does not match statement:\n"
            f"  span:     {sliced!r}\n"
            f"  statement: {req.statement!r}"
        )
