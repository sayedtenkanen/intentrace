"""Tests for the append-only JSONL observation log."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentrace.log import (
    CorruptLineError,
    TornLineError,
    append_observation,
    read_observations,
)
from intentrace.models import Observation


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root with .intentrace directory."""
    (tmp_path / ".intentrace").mkdir()
    return tmp_path


def _make_obs(text: str = "test", session: str = "s1", turn: int = 0) -> Observation:
    return Observation.create(session_id=session, turn_index=turn, kind="prompt", text=text)


def test_round_trip_append_read(repo_root: Path) -> None:
    """Round-trip append/read preserves order and content."""
    obs1 = _make_obs("first", turn=0)
    obs2 = _make_obs("second", turn=1)
    obs3 = _make_obs("third", turn=2)

    append_observation(repo_root, obs1)
    append_observation(repo_root, obs2)
    append_observation(repo_root, obs3)

    result = read_observations(repo_root)
    assert len(result.observations) == 3
    assert result.observations[0].text == "first"
    assert result.observations[1].text == "second"
    assert result.observations[2].text == "third"
    assert result.torn_line is None


def test_torn_final_line_reported(repo_root: Path) -> None:
    """Torn final line is reported as recoverable, not dropped and not fatal."""
    obs = _make_obs("complete")
    append_observation(repo_root, obs)

    # Manually append a torn line
    log_file = repo_root / ".intentrace" / "log.jsonl"
    with open(log_file, "a") as f:
        f.write('{"obs_id":"broken","session_id":"s1","turn":1')  # incomplete JSON

    result = read_observations(repo_root)
    assert len(result.observations) == 1
    assert result.torn_line is not None
    assert isinstance(result.torn_line, TornLineError)
    assert result.observations[0].text == "complete"


def test_corrupt_mid_file_line_raises(repo_root: Path) -> None:
    """A mid-file unparseable line raises CorruptLineError."""
    obs1 = _make_obs("good1")
    obs2 = _make_obs("good2")
    append_observation(repo_root, obs1)
    append_observation(repo_root, obs2)

    # Insert a corrupt line in the middle
    log_file = repo_root / ".intentrace" / "log.jsonl"
    lines = log_file.read_text().splitlines()
    lines.insert(1, "NOT VALID JSON")
    log_file.write_text("\n".join(lines) + "\n")

    with pytest.raises(CorruptLineError):
        read_observations(repo_root)


def test_empty_log(repo_root: Path) -> None:
    """Reading a non-existent log returns empty."""
    result = read_observations(repo_root)
    assert result.observations == []
    assert result.torn_line is None
