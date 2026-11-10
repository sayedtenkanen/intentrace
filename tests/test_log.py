"""Tests for the append-only JSONL observation log."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentrace.log import (
    CorruptLineError,
    TornLineError,
    _versioned_log_path,
    append_decision,
    append_observation,
    log_lock,
    read_observations,
)
from intentrace.models import Decision, Observation


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


def test_append_repairs_a_torn_tail_instead_of_corrupting_the_log(
    repo_root: Path,
) -> None:
    """A partial write must not turn every later append into corruption.

    An interrupted process can leave bytes with no terminating newline.
    The next append truncates that partial record first, so the log keeps
    the records that completed and gains the new one.
    """
    append_observation(repo_root, _make_obs("before"))

    log_file = repo_root / ".intentrace" / "log.jsonl"
    with open(log_file, "a") as f:
        f.write('{"obs_id":"partial","session_id":"s1"')  # interrupted, no newline

    append_observation(repo_root, _make_obs("after", turn=1))

    result = read_observations(repo_root)
    assert result.corrupt_line is None
    assert result.torn_line is None
    assert [o.text for o in result.observations] == ["before", "after"]
    assert log_file.read_bytes().endswith(b"\n")


def test_log_lock_is_reentrant(repo_root: Path) -> None:
    """A caller may hold the lock and still append through the normal API."""
    with log_lock(repo_root):
        append_observation(repo_root, _make_obs("inside"))

    result = read_observations(repo_root)
    assert [o.text for o in result.observations] == ["inside"]


def test_concurrent_appends_do_not_interleave(repo_root: Path) -> None:
    """Threads sharing a repo serialize appends; no line is torn."""
    import threading

    def write(index: int) -> None:
        for turn in range(10):
            append_observation(repo_root, _make_obs(f"t{index}-{turn}", turn=turn))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    result = read_observations(repo_root)
    assert result.corrupt_line is None
    assert result.torn_line is None
    assert len(result.observations) == 40


def test_empty_log(repo_root: Path) -> None:
    """Reading a non-existent log returns empty."""
    result = read_observations(repo_root)
    assert result.observations == []
    assert result.torn_line is None


# Rollback compatibility: records an older binary cannot parse are kept out
# of the base log.


def _ratify(actor: str = "tester"):
    return Decision.create(kind="ratify", req_id="a" * 64, actor=actor)


def _attest(reason: str = "late-anchor", req_id: str = "a" * 64):
    return Decision.create(
        kind="attest",
        req_id=req_id,
        actor="tester",
        reason=reason,
        baseline_hashes={},
    )


def test_attestation_goes_to_the_versioned_log(repo_root: Path) -> None:
    """The base log keeps only kinds a pre-slice-4 reader can parse."""
    append_decision(repo_root, _ratify())
    append_decision(repo_root, _attest())

    base = (repo_root / ".intentrace" / "log.jsonl").read_text()
    versioned = _versioned_log_path(repo_root).read_text()
    assert '"kind":"ratify"' in base
    assert '"kind":"attest"' not in base
    assert '"kind":"attest"' in versioned

    result = read_observations(repo_root)
    assert [d.kind for d in result.decisions] == ["ratify", "attest"]


def test_base_log_stays_parseable_by_a_pre_slice4_reader(repo_root: Path) -> None:
    """Every base-log line validates against the old closed kind set."""
    from datetime import datetime
    from typing import Literal

    from pydantic import BaseModel

    class LegacyDecision(BaseModel):
        dec_id: str
        kind: Literal["ratify", "waive", "demote", "archive", "migrate"]
        req_id: str
        actor: str
        timestamp: datetime
        settle_id: str = ""
        rationale: str | None = None
        baseline_hashes: dict[str, str] = {}

    append_decision(repo_root, _ratify())
    append_decision(
        repo_root,
        Decision.create(
            kind="attest",
            req_id="b" * 64,
            actor="tester",
            reason="drift-accepted",
            rationale="accepting drift",
            baseline_hashes={},
        ),
    )

    lines = (repo_root / ".intentrace" / "log.jsonl").read_text().splitlines()
    assert len(lines) == 1
    for line in lines:
        LegacyDecision.model_validate_json(line)


def test_versioned_decisions_merge_in_time_order(repo_root: Path) -> None:
    """A rollback ratify appended to the base log keeps its place in time."""
    from datetime import UTC, datetime

    first = _ratify("first")
    first = first.model_copy(update={"timestamp": datetime(2026, 1, 1, tzinfo=UTC)})
    attest = _attest().model_copy(update={"timestamp": datetime(2026, 1, 2, tzinfo=UTC)})
    second = _ratify("second").model_copy(update={"timestamp": datetime(2026, 1, 3, tzinfo=UTC)})

    append_decision(repo_root, first)
    append_decision(repo_root, attest)
    append_decision(repo_root, second)

    base = (repo_root / ".intentrace" / "log.jsonl").read_text()
    assert base.count('"kind":"ratify"') == 2
    assert '"kind":"attest"' not in base

    result = read_observations(repo_root)
    assert [d.kind for d in result.decisions] == ["ratify", "attest", "ratify"]
    assert [d.actor for d in result.decisions] == ["first", "tester", "second"]


def test_versioned_log_repairs_its_own_torn_tail(repo_root: Path) -> None:
    """A partial write in the companion log is repaired before its next append."""
    append_decision(repo_root, _attest(req_id="a" * 64))
    versioned = _versioned_log_path(repo_root)
    with open(versioned, "a") as f:
        f.write('{"dec_id":"partial"')  # interrupted, no newline

    append_decision(repo_root, _attest(req_id="b" * 64))

    result = read_observations(repo_root)
    assert result.torn_line is None
    assert result.corrupt_line is None
    assert [d.req_id for d in result.decisions] == ["a" * 64, "b" * 64]
