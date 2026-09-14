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
    r1 = extractor.extract([obs])
    r2 = extractor.extract([obs])
    assert [r.req_id for r in r1.requirements] == [r.req_id for r in r2.requirements]


def test_provenance_span_exact(extractor: FakeExtractor) -> None:
    """Provenance spans, sliced out of source text, exactly reproduce the statement."""
    text = "The system must handle errors gracefully."
    obs = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=text)
    result = extractor.extract([obs])
    reqs = result.requirements
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
    result = extractor.extract([obs])
    assert len(result.requirements) == 0


def test_only_prompts(extractor: FakeExtractor) -> None:
    """Only prompt observations are considered."""
    obs = Observation.create(
        session_id="s1", turn_index=0, kind="tool_result", text="The system must handle errors."
    )
    result = extractor.extract([obs])
    assert len(result.requirements) == 0


def test_multiple_sentences(extractor: FakeExtractor) -> None:
    """Multiple normative sentences produce multiple requirements."""
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="Errors must be logged. The system should never crash silently.",
    )
    result = extractor.extract([obs])
    assert len(result.requirements) == 2


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
    result = extractor.extract(observations)
    requirements = result.requirements

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


def test_quoted_slice_has_no_surrounding_whitespace() -> None:
    """For every requirement, the quoted slice stripped equals the statement."""
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
    result = extractor.extract(observations)
    requirements = result.requirements

    obs_map = {obs.obs_id: obs for obs in observations}

    for req in requirements:
        span = req.provenance[0]
        obs = obs_map[span.obs_id]
        quoted = obs.text[span.start : span.end]
        # The stripped quoted text should have no leading/trailing whitespace
        assert quoted == quoted.strip(), (
            f"quoted span has surrounding whitespace:\n  quoted: {quoted!r}"
        )
        # And it should equal the statement (which is normalized whitespace)
        assert " ".join(quoted.split()) == req.statement


def test_stored_statement_preserves_case_and_punctuation() -> None:
    """R2: stored statement retains original case and trailing punctuation."""
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The system MUST expose stack traces to the user.",
    )
    extractor = FakeExtractor()
    result = extractor.extract([obs])
    reqs = result.requirements
    assert len(reqs) == 1
    req = reqs[0]
    # Stored statement must preserve original case and punctuation
    assert req.statement == "The system MUST expose stack traces to the user."


def test_same_req_id_for_case_whitespace_punctuation_diffs() -> None:
    """R1+R2: statements differing only in case, trailing punctuation, or whitespace produce the same req_id."""
    from intentrace.models import Requirement, Span

    base_prov = [Span(obs_id="obs1", start=0, end=10)]

    r1 = Requirement.create(
        statement="The system must retry.",
        provenance=base_prov,
        extractor_version="fake-v1",
    )
    r2 = Requirement.create(
        statement="  the  system  must  retry  ",
        provenance=base_prov,
        extractor_version="fake-v1",
    )
    r3 = Requirement.create(
        statement="The System Must Retry!",
        provenance=base_prov,
        extractor_version="fake-v1",
    )
    # All three should have the same req_id
    assert r1.req_id == r2.req_id == r3.req_id


def test_different_req_id_for_normative_marker_diff() -> None:
    """R1+R2: must vs should produces different req_ids (normative weight differs)."""
    from intentrace.models import Requirement, Span

    base_prov = [Span(obs_id="obs1", start=0, end=10)]

    r_must = Requirement.create(
        statement="The system must retry.",
        provenance=base_prov,
        extractor_version="fake-v1",
    )
    r_should = Requirement.create(
        statement="The system should retry.",
        provenance=base_prov,
        extractor_version="fake-v1",
    )
    assert r_must.req_id != r_should.req_id
