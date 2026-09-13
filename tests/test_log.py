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
    """Torn final line (no newline) is reported as recoverable."""
    obs = _make_obs("complete")
    append_observation(repo_root, obs)

    # Append a torn line — no trailing newline means truncated append
    log_file = repo_root / ".intentrace" / "log.jsonl"
    with open(log_file, "a") as f:
        f.write('{"obs_id":"broken","session_id":"s1","turn":1')  # no newline

    result = read_observations(repo_root)
    assert len(result.observations) == 1
    assert result.torn_line is not None
    assert isinstance(result.torn_line, TornLineError)
    assert result.corrupt_line is None
    assert result.observations[0].text == "complete"


def test_corrupt_final_line_reported(repo_root: Path) -> None:
    """Corrupt final line (has newline but invalid JSON) is reported as corruption."""
    obs = _make_obs("complete")
    append_observation(repo_root, obs)

    # Append a corrupt line — has newline but invalid JSON
    log_file = repo_root / ".intentrace" / "log.jsonl"
    with open(log_file, "a") as f:
        f.write("NOT VALID JSON\n")

    result = read_observations(repo_root)
    assert len(result.observations) == 1
    assert result.corrupt_line is not None
    assert isinstance(result.corrupt_line, CorruptLineError)
    assert result.torn_line is None
    assert result.observations[0].text == "complete"


def test_corrupt_mid_file_line_reported(repo_root: Path) -> None:
    """A mid-file unparseable line is reported via corrupt_line field."""
    obs1 = _make_obs("good1")
    obs2 = _make_obs("good2")
    append_observation(repo_root, obs1)
    append_observation(repo_root, obs2)

    # Insert a corrupt line in the middle
    log_file = repo_root / ".intentrace" / "log.jsonl"
    lines = log_file.read_text().splitlines()
    lines.insert(1, "NOT VALID JSON")
    log_file.write_text("\n".join(lines) + "\n")

    result = read_observations(repo_root)
    assert result.corrupt_line is not None
    assert isinstance(result.corrupt_line, CorruptLineError)
    assert result.corrupt_line.line_number == 2
    # Only the first good observation was parsed before corruption
    assert len(result.observations) == 1


def test_empty_log(repo_root: Path) -> None:
    """Reading a non-existent log returns empty."""
    result = read_observations(repo_root)
    assert result.observations == []
    assert result.torn_line is None
