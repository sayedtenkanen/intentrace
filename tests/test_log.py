"""Tests for the append-only JSONL observation log."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentrace.log import (
    CorruptLineError,
    FoldError,
    TornLineError,
    _companion_log_path,
    _folded_log_path,
    _log_path,
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


# One log: every decision kind shares the base file, in append order.


def _ratify(actor: str = "tester", req_id: str = "a" * 64):
    return Decision.create(kind="ratify", req_id=req_id, actor=actor)


def _attest(
    reason: str = "late-anchor",
    req_id: str = "a" * 64,
    rationale: str | None = None,
):
    return Decision.create(
        kind="attest",
        req_id=req_id,
        actor="tester",
        reason=reason,
        rationale=rationale,
        baseline_hashes={},
    )


def _write_companion(repo_root: Path, decisions: list[Decision]) -> None:
    _companion_log_path(repo_root).write_text(
        "".join(decision.model_dump_json() + "\n" for decision in decisions)
    )


def test_every_decision_kind_goes_to_the_base_log(repo_root: Path) -> None:
    """Attest is not routed anywhere else; one log carries every kind."""
    append_decision(repo_root, _ratify())
    append_decision(repo_root, _attest())

    base = _log_path(repo_root).read_text()
    assert '"kind":"ratify"' in base
    assert '"kind":"attest"' in base
    assert not _companion_log_path(repo_root).exists()

    result = read_observations(repo_root)
    assert [d.kind for d in result.decisions] == ["ratify", "attest"]


def test_unknown_kind_is_read_from_the_single_log(repo_root: Path) -> None:
    """A kind this build does not know is newer, and the rest still loads."""
    append_decision(repo_root, _ratify())
    append_decision(
        repo_root,
        Decision.create(kind="waive-v2", req_id="b" * 64, actor="future"),
    )
    append_decision(repo_root, _attest(req_id="b" * 64))

    result = read_observations(repo_root)
    assert result.corrupt_line is None
    assert result.torn_line is None
    assert [d.kind for d in result.decisions] == ["ratify", "waive-v2", "attest"]
    assert result.decisions[2].req_id == "b" * 64


def test_read_returns_entries_in_append_order_not_timestamp_order(repo_root: Path) -> None:
    """A backwards clock changes display data, never read order."""
    from datetime import UTC, datetime

    first = _ratify("first").model_copy(update={"timestamp": datetime(2026, 1, 2, tzinfo=UTC)})
    second = _ratify("second").model_copy(update={"timestamp": datetime(2026, 1, 1, tzinfo=UTC)})
    append_decision(repo_root, first)
    append_decision(repo_root, second)

    result = read_observations(repo_root)
    assert [d.actor for d in result.decisions] == ["first", "second"]
    assert result.decisions[0].timestamp > result.decisions[1].timestamp


def test_companion_log_is_folded_once_and_kept(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The companion is appended once, renamed unchanged, and never folded twice."""
    append_decision(repo_root, _ratify("base"))
    _write_companion(
        repo_root,
        [
            _attest("late-anchor"),
            _attest("drift-accepted", rationale="accepting the new retry count"),
        ],
    )
    original = _companion_log_path(repo_root).read_bytes()
    base_before = _log_path(repo_root).read_bytes()

    result = read_observations(repo_root)
    assert _log_path(repo_root).read_bytes() == base_before + original
    assert [d.kind for d in result.decisions] == ["ratify", "attest", "attest"]
    assert [d.reason for d in result.decisions] == [None, "late-anchor", "drift-accepted"]

    folded = _folded_log_path(repo_root)
    assert folded.read_bytes() == original
    assert not _companion_log_path(repo_root).exists()
    assert "folded" in capsys.readouterr().err

    again = read_observations(repo_root)
    assert [d.dec_id for d in again.decisions] == [d.dec_id for d in result.decisions]
    assert capsys.readouterr().err == ""
    assert folded.read_bytes() == original


def test_fold_keeps_each_requirements_order(repo_root: Path) -> None:
    """Appending the companion at the end keeps every requirement's own order."""
    a = "a" * 64
    b = "b" * 64
    append_decision(repo_root, _ratify(req_id=a))
    append_decision(repo_root, _ratify(req_id=b))
    _write_companion(
        repo_root,
        [
            _attest(req_id=a),
            _attest(req_id=b),
            _attest(reason="rename", req_id=a),
        ],
    )

    result = read_observations(repo_root)
    a_decisions = [d for d in result.decisions if d.req_id == a]
    b_decisions = [d for d in result.decisions if d.req_id == b]
    assert [d.kind for d in a_decisions] == ["ratify", "attest", "attest"]
    assert [d.reason for d in a_decisions] == [None, "late-anchor", "rename"]
    assert [d.kind for d in b_decisions] == ["ratify", "attest"]


def test_fold_stops_when_order_cannot_be_established(repo_root: Path) -> None:
    """An interleaving this build cannot know is refused, naming the requirement."""
    req_id = "c" * 64
    append_decision(repo_root, _ratify(req_id=req_id))
    append_decision(
        repo_root,
        Decision.create(kind="waive", req_id=req_id, actor="tester"),
    )
    _write_companion(repo_root, [_attest(req_id=req_id)])
    base_before = _log_path(repo_root).read_bytes()
    companion_before = _companion_log_path(repo_root).read_bytes()

    with pytest.raises(FoldError, match=req_id):
        read_observations(repo_root)

    assert _log_path(repo_root).read_bytes() == base_before
    assert _companion_log_path(repo_root).read_bytes() == companion_before
