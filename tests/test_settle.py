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
    "    return  sum(items)\n"
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

    anchor_v1 = build_anchor("app.py", parse_source(CODE_V1.encode()), CODE_V1.encode(), "total")
    assert anchor_v1 is not None
    baseline_hash = anchor_v1.node_hash

    result = ratify_cli(repo, req_id)
    assert result.returncode == 0, result.stderr

    (repo / "app.py").write_text(CODE_V2_DRIFT)
    anchor_v2 = build_anchor(
        "app.py", parse_source(CODE_V2_DRIFT.encode()), CODE_V2_DRIFT.encode(), "total"
    )
    assert anchor_v2 is not None
    current_hash = anchor_v2.node_hash
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
    assert "no sketch" in result.stderr
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    out, _ = capsys.readouterr()
    assert "cannot ratify" in out
    assert "Re-derived candidate:" in out
    assert "R-" in out, "expected the re-derived candidate block"


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

    live_id = extract_first_req_id(repo)
    store = MemoryStore(repo)
    orphaned = store.orphaned_decisions({live_id})
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

    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.symbol import build_symbol_table

    first, second = (
        FakeExtractor()
        .extract(
            read_observations(tmp_path).observations,
            symbols=build_symbol_table(tmp_path),
        )
        .requirements
    )
    assert first.statement.startswith("The total")
    assert second.statement.startswith("The helper")

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
    assert [d.req_id for d in store.get_decisions(first.req_id)] == [first.req_id]
    assert store.get_decisions(second.req_id) == []


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


# Slice 2 remediation — follow-up tests (commits 1 and 2).


def test_ratify_decline_appends_nothing(tmp_path: Path) -> None:
    """T2: answering no at the confirmation records nothing."""
    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    before = log_line_count(repo)
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "ratify", req_id, "--actor", "tester"],
        capture_output=True,
        text=True,
        cwd=repo,
        input="n\n",
    )
    assert result.returncode == 0
    assert "not ratified" in result.stdout
    assert log_line_count(repo) == before


def test_match_req_id_forms() -> None:
    """T2: exact wins, prefixes match, ambiguity and R- form behave."""
    from intentrace.decisions import match_req_id
    from intentrace.models import Requirement, Span

    def req(rid: str) -> Requirement:
        return Requirement(
            req_id=rid,
            statement="s",
            origin="declared",
            maturity="sketch",
            provenance=[Span(obs_id="o", start=0, end=1)],
            derivation={"extractor_version": "v", "timestamp": "2026-01-01T00:00:00Z"},
            anchors=[],
        )

    reqs = [req("a" * 64), req("ab" + "0" * 62)]
    assert [r.req_id for r in match_req_id("a" * 64, reqs)] == ["a" * 64]
    assert [r.req_id for r in match_req_id("ab", reqs)] == ["ab" + "0" * 62]
    assert len(match_req_id("a", reqs)) == 2
    assert match_req_id("ff", reqs) == []
    assert [r.req_id for r in match_req_id("R-ab", reqs)] == ["ab" + "0" * 62]


def test_ratify_ambiguous_prefix_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T2: a prefix matching two requirements is an error, not a guess."""
    import argparse

    import intentrace.cli as cli
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import read_observations
    from intentrace.store import MemoryStore
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path)
    table = build_symbol_table(repo)
    req = (
        FakeExtractor()
        .extract(read_observations(repo).observations, symbols=table)
        .requirements[0]
    )
    monkeypatch.setattr(cli, "match_req_id", lambda _prefix, _reqs: [req, req])
    monkeypatch.chdir(repo)

    rc = cli.cmd_ratify(argparse.Namespace(req_id="a", actor="tester"))
    assert rc == 1
    _, err = capsys.readouterr()
    assert "be more specific" in err
    assert len(MemoryStore(repo).all_decisions) == 0


def test_settle_stale_skips_and_represents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T2: code changed mid-settle is skipped with the fresh candidate shown."""
    import argparse

    import intentrace.cli as cli
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

    rc = cli.cmd_settle(argparse.Namespace(actor="tester"))
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Re-derived candidate:" in out
    assert "0 ratified, 1 skipped" in out
    assert len(MemoryStore(repo).all_decisions) == 0


def test_why_shows_orphaned_decision_fallback(tmp_path: Path) -> None:
    """T2: an orphaned decision about this file surfaces in why, not a miss."""
    from intentrace.log import append_decision
    from intentrace.models import Decision

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert ratify_cli(repo, req_id).returncode == 0
    ghost = Decision.create(
        kind="ratify",
        req_id="e" * 64,
        actor="tester",
        baseline_hashes={"app.py::vanished": "9" * 64},
    )
    append_decision(repo, ghost)

    (repo / "app.py").write_text('"""Billing helpers."""\n\n\ndef helper():\n    return 0\n')

    out = why_cli(repo, "app.py:5")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "orphaned" in out.stdout
    assert "eeeeeeee" in out.stdout


def test_why_shows_detached_anchor(tmp_path: Path) -> None:
    """T2: code unchanged but no longer attached reads unconfirmed, not clean."""
    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    assert ratify_cli(repo, req_id).returncode == 0

    # A second `total` elsewhere makes the bare name ambiguous, so the
    # extractor drops the anchor without any behaviour changing here.
    (repo / "other.py").write_text("def total(items):\n    return sum(items)\n")

    out = why_cli(repo, "app.py:6")
    assert out.returncode == 0
    assert "R-" in out.stdout, "expected a populated requirement block"
    assert "unconfirmed" in out.stdout
    assert "no longer attached" in out.stdout


ANCHORLESS_TEXT = "The system must never lose data."


def test_active_unimplemented_is_surfaced_in_settle(tmp_path: Path) -> None:
    """M1: a ratified anchorless requirement is shown, not silently dropped."""
    from intentrace.decisions import apply_decisions, judge_requirement
    from intentrace.extract.fake import FakeExtractor
    from intentrace.log import append_observation, read_observations
    from intentrace.models import Observation
    from intentrace.store import MemoryStore
    from intentrace.symbol import build_symbol_table

    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(CODE_V1)
    obs = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=ANCHORLESS_TEXT)
    append_observation(tmp_path, obs)

    symbols = build_symbol_table(tmp_path)
    reqs = (
        FakeExtractor()
        .extract(read_observations(tmp_path).observations, symbols=symbols)
        .requirements
    )
    assert len(reqs) == 1
    assert reqs[0].anchors == []
    req_id = reqs[0].req_id

    assert ratify_cli(tmp_path, req_id).returncode == 0

    store = MemoryStore(tmp_path)
    view = apply_decisions(
        FakeExtractor()
        .extract(store.all_observations, symbols=build_symbol_table(tmp_path))
        .requirements,
        store.all_decisions,
    )
    assert view.requirements[0].maturity == "active"
    assert judge_requirement(view.requirements[0], symbols).status == "unimplemented"

    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "settle"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        input="",
    )
    assert result.returncode == 0
    assert "R-" in result.stdout, "expected a populated requirement block"
    assert "unimplemented" in result.stdout
    assert req_id[:8] in result.stdout


def test_ratify_accepts_r_prefix_form(tmp_path: Path) -> None:
    """M4: ratify accepts the R-xxxxxxxx display form as input."""
    from intentrace.store import MemoryStore

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    result = ratify_cli(repo, f"R-{req_id[:8]}")
    assert result.returncode == 0, result.stderr
    assert "ratified" in result.stdout
    assert len(MemoryStore(repo).get_decisions(req_id)) == 1


def test_drift_details_stable_across_hash_seeds(tmp_path: Path) -> None:
    """M3: multi-anchor drift lines print in the same order under any hash seed."""
    import os

    script = (
        "from intentrace.decisions import judge_requirement;"
        "from intentrace.models import AnchorRef, Ratification, Requirement, Span;"
        "import datetime;"
        "paths = ['app.py::zeta', 'app.py::alpha'];"
        "req = Requirement(req_id='x', statement='s', origin='declared',"
        " maturity='active', provenance=[Span(obs_id='o', start=0, end=1)],"
        " derivation={'extractor_version': 'v',"
        " 'timestamp': '2026-01-01T00:00:00Z'},"
        " anchors=[AnchorRef(lang='python', file='app.py', symbol_path=p,"
        " node_kind='function_definition', node_hash='0' * 64) for p in paths],"
        " ratification=Ratification(actor='t',"
        " timestamp=datetime.datetime(2026, 1, 1),"
        " baseline_hashes={p: '1' * 64 for p in paths}));"
        "from intentrace.symbol import SymbolTable;"
        "t = SymbolTable();"
        "[t.by_qualified.update({p: AnchorRef(lang='python', file='app.py',"
        " symbol_path=p, node_kind='function_definition',"
        " node_hash='2' * 64)}) for p in paths];"
        "print([d.symbol_path for d in judge_requirement(req, t).anchors])"
    )
    outputs = set()
    for seed in ("0", "1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1
    assert outputs.pop() == "['app.py::alpha', 'app.py::zeta']"


# Slice 2 review follow-ups.


def test_record_ratification_rejects_concurrent_ratify(tmp_path: Path) -> None:
    """C1: a decision that landed after the view was built is not duplicated."""
    import intentrace.cli as cli
    from intentrace.log import append_decision
    from intentrace.models import Decision
    from intentrace.store import MemoryStore

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)

    recorded = cli._record_ratification(repo, req_id, "tester", "", {"a": "b"})
    assert recorded is not None
    assert recorded.req_id == req_id
    assert log_line_count(repo) == 2

    # A second process recorded while this one was deciding: the re-read
    # at record time must reject instead of appending a duplicate.
    rival = Decision.create(kind="ratify", req_id="other", actor="rival")
    append_decision(repo, rival)
    before = log_line_count(repo)
    assert cli._record_ratification(repo, "other", "tester", "", {}) is None
    assert log_line_count(repo) == before
    assert len(MemoryStore(repo).get_decisions("other")) == 1


def test_settle_race_skips_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1 end to end: a rival ratify during settle is skipped, not doubled."""
    import argparse

    import intentrace.cli as cli
    from intentrace.models import Decision
    from intentrace.store import MemoryStore
    from intentrace.symbol import SymbolTable

    repo = make_repo(tmp_path)
    req_id = extract_first_req_id(repo)
    real_scan = cli.build_symbol_table
    real_append = cli.append_decision
    calls: list[int] = []

    def scanning(root: Path) -> SymbolTable:
        # A concurrent process ratifies after this one displayed and
        # confirmed, but before it records.
        table = real_scan(root)
        if calls:
            real_append(
                root,
                Decision.create(kind="ratify", req_id=req_id, actor="rival", baseline_hashes={}),
            )
        calls.append(1)
        return table

    monkeypatch.setattr(cli, "build_symbol_table", scanning)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.chdir(repo)

    rc = cli.cmd_settle(argparse.Namespace(actor="tester"))
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "became active" in out
    assert "0 ratified, 1 skipped" in out
    store = MemoryStore(repo)
    assert len(store.all_decisions) == 1
    assert store.all_decisions[0].actor == "rival"
