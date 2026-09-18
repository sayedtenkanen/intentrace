"""Tests for slice 2: ratification, settle, and drift verdicts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CODE_V1 = (
    '"""Billing helpers."""\n'
    "\n"
    "\n"
    "def total(items):\n"
    '    """Return the sum."""\n'
    "    return sum(items)\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 0\n"
)

CODE_V2_DRIFT = (
    '"""Billing helpers."""\n'
    "\n"
    "\n"
    "def total(items):\n"
    '    """Return the sum."""\n'
    "    return sum(items) + 1\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 0\n"
)

CODE_V3_REFACTOR = (
    '"""Billing helpers."""\n'
    "\n"
    "\n"
    "def total(items):\n"
    '    """Return the sum of the items given."""\n'
    "    # totals must stay exact; this comment changes nothing\n"
    "    return sum(items)\n"
    "\n"
    "\n"
    "def assistant():\n"
    "    return 0\n"
)

OBS_TEXT = "The total function must return the sum of all items."


def make_repo(tmp_path: Path, code: str = CODE_V1) -> Path:
    """Create a repo with one code file and one normative observation."""
    from intentrace.log import append_observation
    from intentrace.models import Observation

    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(code)
    obs = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=OBS_TEXT)
    append_observation(tmp_path, obs)
    return tmp_path


def extract_first_req_id(repo: Path) -> str:
    """Re-derive requirements in-process and return the first req_id."""
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.symbol import build_symbol_table

    log_result = read_observations(repo)
    assert len(log_result.observations) == 1
    symbols = build_symbol_table(repo)
    result = FakeExtractor().extract(log_result.observations, symbols=symbols)
    assert len(result.requirements) == 1
    return result.requirements[0].req_id


def ratify_cli(repo: Path, req_id: str, actor: str = "tester") -> subprocess.CompletedProcess[str]:
    """Run `intentrace ratify` answering yes to the confirmation."""
    return subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "ratify", req_id, "--actor", actor],
        capture_output=True,
        text=True,
        cwd=repo,
        input="y\n",
    )


def why_cli(repo: Path, target: str) -> subprocess.CompletedProcess[str]:
    """Run `intentrace why`."""
    return subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", target],
        capture_output=True,
        text=True,
        cwd=repo,
    )


def log_line_count(repo: Path) -> int:
    """Count lines in the log file."""
    log_file = repo / ".intentrace" / "log.jsonl"
    if not log_file.exists():
        return 0
    with open(log_file) as f:
        return sum(1 for _ in f)


# The two tests that justify the slice.


def test_planted_drift_is_caught(tmp_path: Path) -> None:
    """Ratify, change behaviour, why reports unconfirmed naming both hashes."""
    from intentrace.anchor.python import build_anchor, parse_source

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)

    baseline_hash = build_anchor(
        "app.py", parse_source(CODE_V1.encode()), CODE_V1.encode(), "total"
    ).node_hash  # type: ignore[union-attr]

    result = ratify_cli(repo, req_id)
    assert result.returncode == 0, result.stderr

    (repo / "app.py").write_text(CODE_V2_DRIFT)
    current_hash = build_anchor(
        "app.py", parse_source(CODE_V2_DRIFT.encode()), CODE_V2_DRIFT.encode(), "total"
    ).node_hash  # type: ignore[union-attr]
    assert baseline_hash != current_hash

    out = why_cli(repo, "app.py:6")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "active" in out.stdout
    assert "unconfirmed" in out.stdout
    assert baseline_hash[:8] in out.stdout
    assert current_hash[:8] in out.stdout


def test_refactor_produces_no_false_drift(tmp_path: Path) -> None:
    """Ratify, then reformat/comment/docstring-edit/rename-sibling: no unconfirmed."""
    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)

    result = ratify_cli(repo, req_id)
    assert result.returncode == 0, result.stderr

    (repo / "app.py").write_text(CODE_V3_REFACTOR)

    out = why_cli(repo, "app.py:6")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "active" in out.stdout
    assert "unconfirmed" not in out.stdout
    assert "orphaned" not in out.stdout


# Decision log and replay.


def test_ratify_decision_survives_restart(tmp_path: Path) -> None:
    """A ratify decision is appended to the log and visible after replay."""
    from intentrace.decisions import apply_decisions
    from intentrace.store import MemoryStore

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert log_line_count(repo) == 1

    result = ratify_cli(repo, req_id)
    assert result.returncode == 0, result.stderr
    assert log_line_count(repo) == 2

    # Fresh process equivalent: rebuild the store from the log file.
    store = MemoryStore(repo)
    decisions = store.get_decisions(req_id)
    assert len(decisions) == 1
    assert decisions[0].kind == "ratify"
    assert decisions[0].req_id == req_id
    assert decisions[0].actor == "tester"
    assert decisions[0].baseline_hashes.get("app.py::total")

    # Maturity is derived, not stored: fresh extraction is a sketch until
    # decisions are applied.
    from intentrace.extract.fake import FakeExtractor
    from intentrace.symbol import build_symbol_table

    symbols = build_symbol_table(repo)
    fresh = FakeExtractor().extract(store.all_observations, symbols=symbols)
    assert fresh.requirements[0].maturity == "sketch"
    view = apply_decisions(fresh.requirements, store.all_decisions)
    assert view.requirements[0].maturity == "active"
    assert view.orphaned == []


def test_ratify_unknown_id_errors(tmp_path: Path) -> None:
    """Ratifying an id no sketch has fails without appending."""
    repo = make_repo(tmp_path)
    before = log_line_count(repo)
    result = ratify_cli(repo, "deadbeef")
    assert result.returncode != 0
    assert log_line_count(repo) == before


def test_ratify_twice_appends_once(tmp_path: Path) -> None:
    """Ratifying an already-active requirement is a no-op, not a duplicate."""
    from intentrace.store import MemoryStore

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert ratify_cli(repo, req_id).returncode == 0
    second = ratify_cli(repo, req_id)
    assert second.returncode == 0
    assert "already active" in second.stdout
    store = MemoryStore(repo)
    assert len(store.get_decisions(req_id)) == 1


def test_freshness_refusal_represents_candidate(tmp_path: Path) -> None:
    """A proposal made against old code cannot be ratified; fresh is shown."""
    from intentrace.decisions import Stale, check_fresh
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path)
    symbols_v1 = build_symbol_table(repo)
    obs = read_observations(repo).observations
    proposal = FakeExtractor().extract(obs, symbols=symbols_v1).requirements[0]
    assert len(proposal.anchors) == 1

    (repo / "app.py").write_text(CODE_V2_DRIFT)
    symbols_v2 = build_symbol_table(repo)

    outcome = check_fresh(proposal, symbols_v2)
    assert isinstance(outcome, Stale)
    assert len(outcome.fresh_anchors) == 1
    assert outcome.fresh_anchors[0].node_hash != proposal.anchors[0].node_hash


def test_ratify_cli_refuses_stale_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: code changed between proposal display and record is refused."""
    import intentrace.cli as cli
    from intentrace.log import read_observations
    from intentrace.store import MemoryStore
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path)
    table_v1 = build_symbol_table(repo)
    (repo / "app.py").write_text(CODE_V2_DRIFT)
    table_v2 = build_symbol_table(repo)

    calls = iter([table_v1, table_v2])
    monkeypatch.setattr(cli, "build_symbol_table", lambda _root: next(calls))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.chdir(repo)

    import argparse

    obs = read_observations(repo).observations
    assert len(obs) == 1
    # The req_id is stable across the code change (anchors excluded from identity).
    from intentrace.extract.fake import FakeExtractor

    req_id = FakeExtractor().extract(obs, symbols=table_v1).requirements[0].req_id
    args = argparse.Namespace(req_id=req_id, actor="tester")
    rc = cli.cmd_ratify(args)
    assert rc == 1
    assert len(MemoryStore(repo).all_decisions) == 0


def test_orphaned_decision_is_reported(tmp_path: Path) -> None:
    """A decision for an id the extractor no longer produces is reported."""
    from intentrace.log import append_decision
    from intentrace.models import Decision
    from intentrace.store import MemoryStore

    repo = make_repo(tmp_path)
    ghost = Decision.create(
        kind="ratify",
        req_id="f" * 64,
        actor="tester",
        baseline_hashes={"app.py::total": "0" * 64},
    )
    append_decision(repo, ghost)

    store = MemoryStore(repo)
    orphaned = store.orphaned_decisions({"whatever-is-current"})
    assert [d.req_id for d in orphaned] == ["f" * 64]

    # Settle surfaces it instead of dropping it.
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "settle"],
        capture_output=True,
        text=True,
        cwd=repo,
        input="",
    )
    assert result.returncode == 0
    assert "orphaned" in result.stdout.lower()
    assert "ffffffff" in result.stdout


# why honesty.


def test_why_never_prints_satisfied_or_locked(tmp_path: Path) -> None:
    """Even after ratification, why prints neither satisfied nor locked."""
    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert ratify_cli(repo, req_id).returncode == 0

    out = why_cli(repo, "app.py:6")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "satisfied" not in out.stdout.lower()
    assert "locked" not in out.stdout.lower()


def test_why_shows_orphaned_anchor(tmp_path: Path) -> None:
    """Ratify, then delete the symbol: why reports orphaned, not a miss."""
    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert ratify_cli(repo, req_id).returncode == 0

    (repo / "app.py").write_text('"""Billing helpers."""\n\n\ndef helper():\n    return 0\n')

    out = why_cli(repo, "app.py:5")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "orphaned" in out.stdout


# settle behaviour.


def test_settle_empty_queue_changes_nothing(tmp_path: Path) -> None:
    """Settle with no sketches says so and appends nothing."""
    from intentrace.log import append_observation
    from intentrace.models import Observation

    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text("x = 1\n")
    obs = Observation.create(
        session_id="s1", turn_index=0, kind="prompt", text="Please tidy this up."
    )
    append_observation(tmp_path, obs)
    before = log_line_count(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "settle"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        input="",
    )
    assert result.returncode == 0
    assert "nothing to settle" in result.stdout.lower()
    assert log_line_count(tmp_path) == before


def test_settle_ratifies_one_at_a_time(tmp_path: Path) -> None:
    """Two sketches, answers y then n: one decision, one sketch left."""
    from intentrace.log import append_observation
    from intentrace.models import Observation
    from intentrace.store import MemoryStore

    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(CODE_V1)
    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text=(
            "The total function must return the sum. "
            "The helper function should always return zero."
        ),
    )
    append_observation(tmp_path, obs)

    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "settle", "--actor", "tester"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        input="y\nn\n",
    )
    assert result.returncode == 0
    assert "1 ratified" in result.stdout

    store = MemoryStore(tmp_path)
    assert len(store.all_decisions) == 1


def test_no_bulk_ratify_flag() -> None:
    """I2: there is no --all flag on settle or ratify."""
    for cmd in ("settle", "ratify"):
        result = subprocess.run(
            [sys.executable, "-m", "intentrace.cli", cmd, "--help"],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        assert result.returncode == 0
        assert "--all" not in result.stdout
