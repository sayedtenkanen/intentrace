"""Slice 4 exit tests and violation classes, written before the implementation.

Exit 1: late anchor, attest, drift, attest-again rationale.
Exit 2: version bump keeps ratification active and does not re-propose.
Exit 3: recursive rename is orphaned, proposed by settle, attested.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

FROB = "The frobnicate function must retry twice."

CODE_EMPTY = '"""Nothing yet."""\n'

CODE_FROB = '"""Service."""\n\n\ndef frobnicate():\n    return 1\n'

CODE_FROB_CHANGED = '"""Service."""\n\n\ndef frobnicate():\n    return 2\n'

CODE_RECURSIVE = '"""Countdown."""\n\n\ndef countdown(n):\n    if n:\n        countdown(n - 1)\n'

CODE_RENAMED = '"""Countdown."""\n\n\ndef count_up(n):\n    if n:\n        count_up(n - 1)\n'

OBS_RECURSIVE = "The countdown function must stop at zero."


def make_repo(tmp_path: Path, code: str, text: str) -> Path:
    from intentrace.log import append_observation
    from intentrace.models import Observation

    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(code)
    append_observation(
        tmp_path,
        Observation.create(session_id="s1", turn_index=0, kind="prompt", text=text),
    )
    return tmp_path


def add_observation(repo: Path, text: str, turn: int = 1) -> None:
    from intentrace.log import append_observation
    from intentrace.models import Observation

    append_observation(
        repo,
        Observation.create(session_id="s1", turn_index=turn, kind="prompt", text=text),
    )


def derive(repo: Path):
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.symbol import build_symbol_table

    return (
        FakeExtractor()
        .extract(read_observations(repo).observations, symbols=build_symbol_table(repo))
        .requirements
    )


def run_cli(repo: Path, *args: str, input_text: str = "y\n") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "intentrace.cli", *args],
        capture_output=True,
        text=True,
        cwd=repo,
        input=input_text,
    )


def why(repo: Path, target: str = "app.py:5") -> subprocess.CompletedProcess[str]:
    return run_cli(repo, "why", target, input_text="")


def decisions(repo: Path):
    from intentrace.store import MemoryStore

    return MemoryStore(repo).all_decisions


def view(repo: Path):
    from intentrace.decisions import apply_decisions
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.symbol import build_symbol_table

    symbols = build_symbol_table(repo)
    fresh = FakeExtractor().extract(read_observations(repo).observations, symbols=symbols)
    return apply_decisions(fresh.requirements, read_observations(repo).decisions, symbols)


# Exit 1.


def test_late_anchor_attest_then_drift_requires_rationale(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_EMPTY, FROB)
    req_id = derive(repo)[0].req_id

    ratified = run_cli(repo, "ratify", req_id, "--actor", "tester")
    assert ratified.returncode == 0, ratified.stderr
    snapshot = decisions(repo)[0].snapshot
    assert snapshot is not None
    assert snapshot.statement == FROB

    # Code arrives after ratification: the anchor is late, not silently clean.
    (repo / "app.py").write_text(CODE_FROB)
    out = why(repo, "app.py:5")
    assert out.returncode == 0
    assert "unconfirmed" in out.stdout
    assert "pending" in out.stdout

    attested = run_cli(repo, "attest", req_id, "--actor", "tester")
    assert attested.returncode == 0, attested.stderr
    attest_decision = decisions(repo)[-1]
    assert attest_decision.kind == "attest"
    assert attest_decision.reason == "late-anchor"
    assert attest_decision.baseline_hashes.get("app.py::frobnicate")

    clean = why(repo, "app.py:5")
    assert "unconfirmed" not in clean.stdout
    assert "orphaned" not in clean.stdout

    # Behaviour changes: drift is reported with both hashes.
    (repo / "app.py").write_text(CODE_FROB_CHANGED)
    drifted = why(repo, "app.py:5")
    assert "unconfirmed" in drifted.stdout
    baseline = attest_decision.baseline_hashes["app.py::frobnicate"]
    assert baseline[:8] in drifted.stdout

    # Acknowledging drift without a reason is refused.
    refused = run_cli(repo, "attest", req_id)
    assert refused.returncode != 0
    assert "rationale" in (refused.stdout + refused.stderr).lower()
    assert len(decisions(repo)) == 2

    accepted = run_cli(repo, "attest", req_id, "--rationale", "accepting the new retry count")
    assert accepted.returncode == 0, accepted.stderr
    final = decisions(repo)[-1]
    assert final.reason == "drift-accepted"
    assert final.rationale == "accepting the new retry count"

    shown = why(repo, "app.py:5")
    assert "attested" in shown.stdout
    assert "drift-accepted" in shown.stdout


# Exit 2.


def test_version_bump_keeps_ratification_active_and_suppresses_reproposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import intentrace.cli as cli
    import intentrace.extract.fake as fake_mod

    repo = make_repo(tmp_path, CODE_FROB, FROB)
    real_version = fake_mod.VERSION

    monkeypatch.setattr(fake_mod, "VERSION", "fake-v0")
    old_id = derive(repo)[0].req_id
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.chdir(repo)
    assert cli.cmd_ratify(argparse.Namespace(req_id=old_id, actor="tester")) == 0
    capsys.readouterr()

    monkeypatch.setattr(fake_mod, "VERSION", real_version)
    loaded = cli._load_view(repo)
    assert loaded is not None
    active = [r for r in loaded.requirements if r.maturity == "active"]
    assert [r.req_id for r in active] == [old_id]
    assert active[0].statement == FROB
    assert [r for r in loaded.requirements if r.maturity == "sketch"] == []

    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert cli.cmd_settle(argparse.Namespace(actor="tester")) == 0
    out, _ = capsys.readouterr()
    assert "nothing to settle" in out.lower()
    assert "suppressed" in out.lower()


# Exit 3.


def test_recursive_rename_is_proposed_and_attested(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_RECURSIVE, OBS_RECURSIVE)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id, "--actor", "tester").returncode == 0

    (repo / "app.py").write_text(CODE_RENAMED)
    orphaned = why(repo, "app.py:5")
    assert "orphaned" in orphaned.stdout

    settled = run_cli(repo, "settle", "--actor", "tester", input_text="y\n")
    assert settled.returncode == 0, settled.stderr
    assert "count_up" in settled.stdout
    assert "rename" in settled.stdout

    final = decisions(repo)[-1]
    assert final.kind == "attest"
    assert final.reason == "rename"
    assert "app.py::count_up" in final.baseline_hashes

    clean = why(repo, "app.py:5")
    assert "unconfirmed" not in clean.stdout
    assert "orphaned" not in clean.stdout


# Violation classes.


def test_legacy_ratification_without_snapshot_is_orphaned_with_reason(tmp_path: Path) -> None:
    from intentrace.log import append_decision
    from intentrace.models import Decision

    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    append_decision(
        repo,
        Decision.create(
            kind="ratify",
            req_id=req_id,
            actor="tester",
            baseline_hashes={"app.py::frobnicate": "0" * 64},
        ),
    )
    loaded = view(repo)
    assert [d.req_id for d in loaded.orphaned] == [req_id]
    assert loaded.requirements[0].maturity == "sketch"


def test_unknown_decision_kind_is_not_corruption(tmp_path: Path) -> None:
    from intentrace.log import _log_path, read_observations

    repo = make_repo(tmp_path, CODE_FROB, FROB)
    line = {
        "dec_id": "a" * 64,
        "kind": "waive-v2",
        "req_id": "b" * 64,
        "actor": "future",
        "timestamp": "2026-11-10T00:00:00Z",
        "baseline_hashes": {},
    }
    with open(_log_path(repo), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    result = read_observations(repo)
    assert result.corrupt_line is None
    assert result.decisions[0].kind == "waive-v2"

    settled = run_cli(repo, "settle", input_text="")
    assert settled.returncode == 0
    assert "newer version" in settled.stdout


def test_suppressed_sketch_is_visible(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0

    loaded = view(repo)
    assert len(loaded.suppressed) == 1
    settled = run_cli(repo, "settle", input_text="")
    assert "suppressed" in settled.stdout.lower()


def test_same_statement_in_second_observation_is_not_ratified_twice(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    add_observation(repo, FROB, turn=1)

    loaded = view(repo)
    assert [r.maturity for r in loaded.requirements] == ["active"]
    # The original utterance's sketch and the duplicate are both covered.
    assert len(loaded.suppressed) == 2
    assert {s.reason for s in loaded.suppressed} == {"same span", "same statement"}


def test_open_drift_verdict_is_offered_as_attest_item(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    (repo / "app.py").write_text(CODE_FROB_CHANGED)

    before = len(decisions(repo))
    settled = run_cli(repo, "settle", input_text="")
    assert settled.returncode == 0
    assert req_id[:8] in settled.stdout
    assert "attest" in settled.stdout.lower()
    assert len(decisions(repo)) == before


def test_no_bulk_attest_flag() -> None:
    result = run_cli(Path.cwd(), "attest", "--help", input_text="")
    assert result.returncode == 0
    assert "--all" not in result.stdout
