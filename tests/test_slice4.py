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

CODE_FROB_DELETED = '"""Service."""\n'

CODE_RECURSIVE = '"""Countdown."""\n\n\ndef countdown(n):\n    if n:\n        countdown(n - 1)\n'

CODE_RENAMED = '"""Countdown."""\n\n\ndef count_up(n):\n    if n:\n        count_up(n - 1)\n'

OBS_RECURSIVE = "The countdown function must stop at zero."

CODE_TWO = '"""Two."""\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n'

CODE_TWO_RENAMED = '"""Two."""\n\n\ndef gamma():\n    return 1\n\n\ndef delta():\n    return 2\n'

CODE_FROB_RENAMED_CHANGED = '"""Service."""\n\n\ndef fuzz():\n    return 2\n'

CODE_DUPLICATE_BODIES = (
    '"""Two."""\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 1\n'
)

CODE_DUPLICATE_BODIES_RENAMED = (
    '"""Two."""\n\n\ndef gamma():\n    return 1\n\n\ndef delta():\n    return 1\n'
)


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
    # Both hashes are reported: the baseline alone would not show what the
    # code is now.
    from intentrace.symbol import build_symbol_table

    current = build_symbol_table(repo).by_qualified["app.py::frobnicate"].node_hash
    assert current[:8] in drifted.stdout

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
    # The same requirement must not also be reported as unimplemented.
    assert "unimplemented" not in settled.stdout

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

    # The report says why, so the reader does not look for a version bump.
    settled = run_cli(repo, "settle", input_text="")
    assert settled.returncode == 0
    assert "snapshot" in settled.stdout.lower()


def test_legacy_ratification_can_be_superseded_by_ratifying_again(tmp_path: Path) -> None:
    """A decision with no snapshot must not block re-ratifying its sketch.

    Otherwise the requirement is reported orphaned while ratify answers
    "already active" and no recorded act can ever make it active.
    """
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

    ratified = run_cli(repo, "ratify", req_id, "--actor", "tester")
    assert ratified.returncode == 0, ratified.stderr
    assert "already active" not in ratified.stdout.lower()
    assert "ratified" in ratified.stdout

    loaded = view(repo)
    active = [r for r in loaded.requirements if r.maturity == "active"]
    assert [r.req_id for r in active] == [req_id]
    assert loaded.orphaned == []
    assert len(decisions(repo)) == 2


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


def test_one_attest_cannot_drop_another_missing_anchor(tmp_path: Path) -> None:
    """Two orphaned anchors need two replacements, one per --anchor.

    Attesting a single rename must not quietly remove the other missing
    anchor from the baseline: that would accept a disappearance no human
    recorded.
    """
    from intentrace.log import append_decision
    from intentrace.models import Decision, Observation, RequirementSnapshot, Span
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path, CODE_TWO, FROB)
    symbols = build_symbol_table(repo)
    alpha = symbols.by_qualified["app.py::alpha"]
    beta = symbols.by_qualified["app.py::beta"]
    obs_id = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=FROB).obs_id
    req_id = "c" * 64
    append_decision(
        repo,
        Decision.create(
            kind="ratify",
            req_id=req_id,
            actor="tester",
            baseline_hashes={
                alpha.symbol_path: alpha.node_hash,
                beta.symbol_path: beta.node_hash,
            },
            baseline_name_hashes={
                alpha.symbol_path: alpha.name_hash,
                beta.symbol_path: beta.name_hash,
            },
            snapshot=RequirementSnapshot(
                statement=FROB,
                provenance=[Span(obs_id=obs_id, start=0, end=len(FROB))],
                extractor_version="fake-v2",
            ),
        ),
    )
    (repo / "app.py").write_text(CODE_TWO_RENAMED)

    partial = run_cli(repo, "attest", req_id, "--anchor", "app.py::gamma")
    assert partial.returncode != 0
    assert "one --anchor per missing anchor" in (partial.stdout + partial.stderr).lower()
    assert len(decisions(repo)) == 1

    both = run_cli(
        repo,
        "attest",
        req_id,
        "--anchor",
        "app.py::gamma",
        "--anchor",
        "app.py::delta",
    )
    assert both.returncode == 0, both.stderr
    final = decisions(repo)[-1]
    assert final.kind == "attest"
    assert final.reason == "rename"
    assert set(final.baseline_hashes) == {"app.py::gamma", "app.py::delta"}


def test_deleted_anchor_needs_a_rationale_and_becomes_unimplemented(
    tmp_path: Path,
) -> None:
    """A vanished anchor with no replacement has an exit, not a dead end.

    Accepting the disappearance is drift-accepted and needs a rationale.
    Afterwards the obligation is unimplemented, not orphaned forever.
    """
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    (repo / "app.py").write_text(CODE_FROB_DELETED)

    refused = run_cli(repo, "attest", req_id)
    assert refused.returncode != 0
    assert "rationale" in (refused.stdout + refused.stderr).lower()
    assert len(decisions(repo)) == 1

    accepted = run_cli(repo, "attest", req_id, "--rationale", "frobnicate was removed")
    assert accepted.returncode == 0, accepted.stderr
    final = decisions(repo)[-1]
    assert final.reason == "drift-accepted"
    assert final.baseline_hashes == {}

    settled = run_cli(repo, "settle", input_text="")
    assert "orphaned" not in settled.stdout
    assert "unimplemented" in settled.stdout

    # The obligation can be re-attached when the code comes back, by a
    # recorded act rather than by a silent extractor match.
    (repo / "app.py").write_text(CODE_FROB)
    reattached = run_cli(repo, "attest", req_id, "--anchor", "app.py::frobnicate")
    assert reattached.returncode == 0, reattached.stderr
    assert decisions(repo)[-1].reason == "late-anchor"
    assert "app.py::frobnicate" in decisions(repo)[-1].baseline_hashes


def test_drift_outranks_a_simultaneous_rename(tmp_path: Path) -> None:
    """A changed body is drift-accepted even when another anchor was renamed.

    The tool assigns the reason, so a rename elsewhere cannot make the
    acknowledgement cheaper than the drift it also accepts.
    """
    from intentrace.log import append_decision
    from intentrace.models import Decision, Observation, RequirementSnapshot, Span
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path, CODE_TWO, FROB)
    symbols = build_symbol_table(repo)
    alpha = symbols.by_qualified["app.py::alpha"]
    beta = symbols.by_qualified["app.py::beta"]
    obs_id = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=FROB).obs_id
    req_id = "d" * 64
    append_decision(
        repo,
        Decision.create(
            kind="ratify",
            req_id=req_id,
            actor="tester",
            baseline_hashes={
                alpha.symbol_path: alpha.node_hash,
                beta.symbol_path: beta.node_hash,
            },
            baseline_name_hashes={
                alpha.symbol_path: alpha.name_hash,
                beta.symbol_path: beta.name_hash,
            },
            snapshot=RequirementSnapshot(
                statement=FROB,
                provenance=[Span(obs_id=obs_id, start=0, end=len(FROB))],
                extractor_version="fake-v2",
            ),
        ),
    )
    (repo / "app.py").write_text(
        '"""Two."""\n\n\ndef gamma():\n    return 1\n\n\ndef beta():\n    return 3\n'
    )

    refused = run_cli(repo, "attest", req_id)
    assert refused.returncode != 0
    assert "rationale" in (refused.stdout + refused.stderr).lower()
    assert len(decisions(repo)) == 1

    accepted = run_cli(repo, "attest", req_id, "--rationale", "beta now returns 3")
    assert accepted.returncode == 0, accepted.stderr
    final = decisions(repo)[-1]
    assert final.reason == "drift-accepted"
    assert set(final.baseline_hashes) == {"app.py::gamma", "app.py::beta"}


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


# Review pass 2 — findings the slice-4 review raised.


def test_manual_replacement_with_a_changed_body_needs_a_rationale(tmp_path: Path) -> None:
    """A renamed-and-modified --anchor is drift, not a clean rename."""
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    (repo / "app.py").write_text(CODE_FROB_RENAMED_CHANGED)

    refused = run_cli(repo, "attest", req_id, "--anchor", "app.py::fuzz")
    assert refused.returncode != 0
    assert "rationale" in (refused.stdout + refused.stderr).lower()
    assert len(decisions(repo)) == 1

    accepted = run_cli(
        repo,
        "attest",
        req_id,
        "--anchor",
        "app.py::fuzz",
        "--rationale",
        "renamed and the retry count changed",
    )
    assert accepted.returncode == 0, accepted.stderr
    final = decisions(repo)[-1]
    assert final.reason == "drift-accepted"
    assert set(final.baseline_hashes) == {"app.py::fuzz"}


def test_duplicate_name_hashes_do_not_block_replacement_mapping(tmp_path: Path) -> None:
    """One --anchor per missing anchor is enough even with equal hashes."""
    from intentrace.log import append_decision
    from intentrace.models import Decision, Observation, RequirementSnapshot, Span
    from intentrace.symbol import build_symbol_table

    repo = make_repo(tmp_path, CODE_DUPLICATE_BODIES, FROB)
    symbols = build_symbol_table(repo)
    alpha = symbols.by_qualified["app.py::alpha"]
    beta = symbols.by_qualified["app.py::beta"]
    assert alpha.name_hash == beta.name_hash
    obs_id = Observation.create(session_id="s1", turn_index=0, kind="prompt", text=FROB).obs_id
    req_id = "e" * 64
    append_decision(
        repo,
        Decision.create(
            kind="ratify",
            req_id=req_id,
            actor="tester",
            baseline_hashes={
                alpha.symbol_path: alpha.node_hash,
                beta.symbol_path: beta.node_hash,
            },
            baseline_name_hashes={
                alpha.symbol_path: alpha.name_hash,
                beta.symbol_path: beta.name_hash,
            },
            snapshot=RequirementSnapshot(
                statement=FROB,
                provenance=[Span(obs_id=obs_id, start=0, end=len(FROB))],
                extractor_version="fake-v2",
            ),
        ),
    )
    (repo / "app.py").write_text(CODE_DUPLICATE_BODIES_RENAMED)

    attested = run_cli(
        repo,
        "attest",
        req_id,
        "--anchor",
        "app.py::gamma",
        "--anchor",
        "app.py::delta",
    )
    assert attested.returncode == 0, attested.stderr
    final = decisions(repo)[-1]
    assert final.reason == "rename"
    assert set(final.baseline_hashes) == {"app.py::gamma", "app.py::delta"}


def test_same_statement_is_ratified_once_within_one_settle_run(tmp_path: Path) -> None:
    """Two observations with the same words do not create two obligations."""
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    add_observation(repo, FROB, turn=1)

    settled = run_cli(repo, "settle", "--actor", "tester", input_text="y\n")
    assert settled.returncode == 0, settled.stderr
    assert "already covered" in settled.stdout
    assert len(decisions(repo)) == 1


def test_attest_refuses_a_plan_that_went_stale_while_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Code changed after the plan was shown must be re-planned, not recorded."""
    import intentrace.cli as cli

    repo = make_repo(tmp_path, CODE_EMPTY, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    (repo / "app.py").write_text(CODE_FROB)

    def answer_and_change(_prompt: str) -> str:
        (repo / "app.py").write_text(CODE_FROB_CHANGED)
        return "y"

    monkeypatch.setattr("builtins.input", answer_and_change)
    monkeypatch.chdir(repo)
    rc = cli.cmd_attest(
        argparse.Namespace(req_id=req_id, anchor=None, rationale=None, actor="tester")
    )
    assert rc == 1
    out, err = capsys.readouterr()
    assert "changed while deciding" in (out + err)
    assert len(decisions(repo)) == 1


def test_scan_error_blocks_attestation(tmp_path: Path) -> None:
    """An unreadable file must not turn its anchors into accepted drops."""
    repo = make_repo(tmp_path, CODE_FROB, FROB)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id).returncode == 0
    (repo / "broken.py").mkdir()

    refused = run_cli(repo, "attest", req_id, "--rationale", "anything")
    assert refused.returncode != 0
    assert "broken.py" in (refused.stdout + refused.stderr)
    assert len(decisions(repo)) == 1


def test_legacy_orphan_is_shown_by_why_alongside_its_sketch(tmp_path: Path) -> None:
    """A matching sketch must not hide a pre-snapshot ratification."""
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

    out = why(repo)
    assert "sketch" in out.stdout
    assert "orphaned decision" in out.stdout
    assert "snapshot" in out.stdout.lower()


def test_latest_snapshot_ratification_wins() -> None:
    """Replay uses the most recent ratify, so a newer baseline is not lost."""
    from intentrace.decisions import apply_decisions
    from intentrace.models import Decision, RequirementSnapshot, Span
    from intentrace.symbol import SymbolTable

    def ratify(actor: str, baseline: dict[str, str]) -> Decision:
        return Decision.create(
            kind="ratify",
            req_id="f" * 64,
            actor=actor,
            baseline_hashes=baseline,
            snapshot=RequirementSnapshot(
                statement="The frobnicate function must retry twice.",
                provenance=[Span(obs_id="o", start=0, end=1)],
                extractor_version="v",
            ),
        )

    older = ratify("first", {"app.py::frobnicate": "1" * 64})
    stale_attest = Decision.create(
        kind="attest",
        req_id="f" * 64,
        actor="first",
        reason="late-anchor",
        baseline_hashes={"app.py::frobnicate": "3" * 64},
    )
    newer = ratify("second", {"app.py::frobnicate": "2" * 64})
    loaded = apply_decisions([], [older, stale_attest, newer], SymbolTable())

    assert loaded.requirements[0].ratification is not None
    assert loaded.requirements[0].ratification.actor == "second"
    assert loaded.requirements[0].ratification.baseline_hashes == {"app.py::frobnicate": "2" * 64}
    assert loaded.requirements[0].ratification.attested_at is None


def test_invalid_attestation_decisions_are_rejected() -> None:
    """A record that would replace a baseline must carry a valid reason."""
    from intentrace.models import Decision

    with pytest.raises(ValueError):
        Decision.create(kind="attest", req_id="a" * 64, actor="tester", reason="clean")
    with pytest.raises(ValueError):
        Decision.create(
            kind="attest",
            req_id="a" * 64,
            actor="tester",
            reason="drift-accepted",
        )
    with pytest.raises(ValueError):
        Decision.create(
            kind="attest",
            req_id="a" * 64,
            actor="tester",
            reason="drift-accepted",
            rationale="   ",
        )

    valid = Decision.create(
        kind="attest",
        req_id="a" * 64,
        actor="tester",
        reason="drift-accepted",
        rationale="retries changed",
    )
    assert valid.reason == "drift-accepted"
