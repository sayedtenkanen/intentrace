"""Slice 4 remediation: append order, one log, history, and the fitness functions.

Written before the fixes. The first two tests reproduce the crash on a
timezone-less timestamp and the replay bug a backwards clock used to cause.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from intentrace.log import append_decision, append_observation, read_observations
from intentrace.models import Decision, Observation, RequirementSnapshot

FROB = "The frobnicate function must retry twice."

CODE_EMPTY = '"""Nothing yet."""\n'

CODE_FROB = '"""Service."""\n\n\ndef frobnicate():\n    return 1\n'

CODE_FROB_CHANGED = '"""Service."""\n\n\ndef frobnicate():\n    return 2\n'

CODE_FROB_CHANGED_AGAIN = '"""Service."""\n\n\ndef frobnicate():\n    return 3\n'


def make_repo(tmp_path: Path, code: str, text: str = FROB) -> Path:
    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(code)
    append_observation(
        tmp_path,
        Observation.create(session_id="s1", turn_index=0, kind="prompt", text=text),
    )
    return tmp_path


def add_observation(repo: Path, text: str, turn: int = 1) -> None:
    append_observation(
        repo,
        Observation.create(session_id="s1", turn_index=turn, kind="prompt", text=text),
    )


def derive(repo: Path):
    from intentrace.extract.fake import FakeExtractor
    from intentrace.symbol import build_symbol_table

    return (
        FakeExtractor()
        .extract(read_observations(repo).observations, symbols=build_symbol_table(repo))
        .requirements
    )


def symbols(repo: Path):
    from intentrace.symbol import build_symbol_table

    return build_symbol_table(repo)


def run_cli(repo: Path, *args: str, input_text: str = "y\n") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "intentrace.cli", *args],
        capture_output=True,
        text=True,
        cwd=repo,
        input=input_text,
    )


def why(repo: Path, target: str = "app.py:5", *extra: str) -> subprocess.CompletedProcess[str]:
    return run_cli(repo, "why", target, *extra, input_text="")


# 1. A valid observation with no timezone must not crash a read.


def test_timezone_less_timestamp_reads_and_ratifies(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_FROB)
    naive = datetime(2026, 11, 1, 10, 0)
    append_observation(
        repo,
        Observation.create(
            session_id="s1",
            turn_index=1,
            kind="prompt",
            text="The frobnicate function must log.",
            timestamp=naive,
        ),
    )

    # Stored as given: no timezone is invented.
    raw = (repo / ".intentrace" / "log.jsonl").read_text()
    assert '"timestamp":"2026-11-01T10:00:00"' in raw
    read = read_observations(repo)
    assert read.observations[1].timestamp == naive
    assert read.observations[1].timestamp.tzinfo is None

    req_id = derive(repo)[0].req_id
    ratified = run_cli(repo, "ratify", req_id, "--actor", "tester")
    assert ratified.returncode == 0, ratified.stderr
    assert "ratified" in ratified.stdout

    shown = why(repo)
    assert shown.returncode == 0, shown.stderr
    assert "2026-11-01" in shown.stdout
    assert "frobnicate" in shown.stdout
    assert "TypeError" not in shown.stderr


# 2. Replay follows append order, not the clock.


def test_clock_stepped_backwards_replays_in_append_order(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_FROB)
    req = derive(repo)[0]
    table = symbols(repo)
    first_hash = table.by_qualified["app.py::frobnicate"].node_hash

    ratify = Decision.create(
        kind="ratify",
        req_id=req.req_id,
        actor="tester",
        baseline_hashes={"app.py::frobnicate": first_hash},
        baseline_name_hashes={
            "app.py::frobnicate": table.by_qualified["app.py::frobnicate"].name_hash
        },
        snapshot=RequirementSnapshot(
            statement=req.statement,
            provenance=req.provenance,
            extractor_version=req.derivation.extractor_version,
        ),
        timestamp=datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
    )
    # A clock that steps backwards writes later acts with earlier stamps.
    late = Decision.create(
        kind="attest",
        req_id=req.req_id,
        actor="tester",
        reason="late-anchor",
        baseline_hashes={"app.py::frobnicate": first_hash},
        timestamp=datetime(2026, 11, 10, 11, 0, tzinfo=UTC),
    )

    (repo / "app.py").write_text(CODE_FROB_CHANGED)
    changed_hash = symbols(repo).by_qualified["app.py::frobnicate"].node_hash
    drift = Decision.create(
        kind="attest",
        req_id=req.req_id,
        actor="tester",
        reason="drift-accepted",
        rationale="accepting the new retry count",
        baseline_hashes={"app.py::frobnicate": changed_hash},
        timestamp=datetime(2026, 11, 10, 10, 0, tzinfo=UTC),
    )

    for decision in (ratify, late, drift):
        append_decision(repo, decision)

    logged = read_observations(repo).decisions
    assert [d.reason for d in logged] == [None, "late-anchor", "drift-accepted"]
    assert [d.timestamp for d in logged] != sorted(d.timestamp for d in logged)

    accepted = why(repo)
    assert accepted.returncode == 0, accepted.stderr
    assert "unverified" in accepted.stdout
    assert "unconfirmed" not in accepted.stdout
    assert "drift-accepted" in accepted.stdout

    (repo / "app.py").write_text(CODE_FROB_CHANGED_AGAIN)
    drifted = why(repo)
    assert "unconfirmed" in drifted.stdout
    assert "drift" in drifted.stdout


# 3. One repository holding every state at once, for the fitness functions.

CODE_STATES = '''"""States."""


def clean_fn():
    return 1


def drift_fn():
    return 1


def drift_attested_fn():
    return 1


def orphan_fn():
    return 1


def renamed_fn(n):
    if n:
        renamed_fn(n - 1)


def moved_fn():
    return 1
'''

CODE_STATES_CHANGED = '''"""States."""


def clean_fn():
    return 1


def late_fn():
    return 1


def late_open_fn():
    return 1


def drift_fn():
    return 2


def drift_attested_fn():
    return 2


def renamed_fn2(n):
    if n:
        renamed_fn2(n - 1)
'''

CODE_MOVED = '''"""Moved."""


def moved_fn():
    return 1
'''

STATE_TEXTS = [
    ("clean", "The clean_fn function must stay."),
    ("drift", "The drift_fn function must stay."),
    ("drift_attested", "The drift_attested_fn function must stay."),
    ("orphan", "The orphan_fn function must stay."),
    ("renamed", "The renamed_fn function must stay."),
    ("moved", "The moved_fn function must move."),
    ("late", "The late_fn function must appear later."),
    ("late_open", "The late_open_fn function must appear later too."),
    ("sketch", "The sketch_fn function must be proposed."),
]


def all_states_repo(tmp_path: Path) -> Path:
    """Build one repository holding sketch, clean, late, drift, orphan and attested states."""
    (tmp_path / ".intentrace").mkdir()
    (tmp_path / "app.py").write_text(CODE_STATES)
    for turn, (_, text) in enumerate(STATE_TEXTS):
        add_observation(tmp_path, text, turn=turn)

    by_statement = {req.statement: req.req_id for req in derive(tmp_path)}
    ids = {name: by_statement[text] for name, text in STATE_TEXTS}

    # Ratify every requirement that has code; ratify the late anchor before
    # its function exists; leave the sketch unratified.
    for name in (
        "clean",
        "drift",
        "drift_attested",
        "orphan",
        "renamed",
        "moved",
        "late",
        "late_open",
    ):
        result = run_cli(tmp_path, "ratify", ids[name], "--actor", "tester")
        assert result.returncode == 0, result.stderr
        assert "ratified" in result.stdout

    (tmp_path / "app.py").write_text(CODE_STATES_CHANGED)
    (tmp_path / "other.py").write_text(CODE_MOVED)

    for name in ("late", "renamed", "drift_attested"):
        result = run_cli(tmp_path, "attest", ids[name], "--actor", "tester")
        if name == "drift_attested":
            result = run_cli(
                tmp_path,
                "attest",
                ids[name],
                "--actor",
                "tester",
                "--rationale",
                "accepting the new body",
            )
        assert result.returncode == 0, result.stderr
        assert "attested" in result.stdout

    moved = run_cli(
        tmp_path,
        "attest",
        ids["moved"],
        "--actor",
        "tester",
        "--anchor",
        "other.py::moved_fn",
    )
    assert moved.returncode == 0, moved.stderr
    return tmp_path


def _loaded_states(repo: Path):
    import intentrace.cli as cli

    loaded = cli._load_view(repo)
    assert loaded is not None
    return loaded


def test_ff_no_baselineless_anchor(tmp_path: Path) -> None:
    """No active anchor without a baseline reports a clean status."""
    from intentrace.decisions import judge_requirement

    loaded = _loaded_states(all_states_repo(tmp_path))
    checked = 0
    allowed = 0
    for req in loaded.requirements:
        if req.maturity != "active" or req.ratification is None:
            continue
        verdict = judge_requirement(req, loaded.symbols)
        baseline = req.ratification.baseline_hashes
        for anchor in req.anchors:
            checked += 1
            if anchor.symbol_path in baseline:
                continue
            assert verdict.status in ("unconfirmed", "orphaned"), (
                f"{req.statement}: anchor {anchor.symbol_path} has no baseline "
                f"but reports {verdict.status}"
            )
            allowed += 1
    assert checked > 0
    assert allowed > 0


def test_ff_open_verdicts_have_an_exit(tmp_path: Path) -> None:
    """Every unconfirmed/orphaned requirement is offered with an attest path."""
    import intentrace.cli as cli
    from intentrace.decisions import judge_requirement

    repo = all_states_repo(tmp_path)
    loaded = _loaded_states(repo)
    open_items = [
        (req, judge_requirement(req, loaded.symbols))
        for req in loaded.requirements
        if req.maturity == "active"
        and judge_requirement(req, loaded.symbols).status in ("unconfirmed", "orphaned")
    ]
    assert open_items

    settled = run_cli(repo, "settle", input_text="n\n" * 20)
    assert settled.returncode == 0, settled.stderr
    assert "open verdicts to attest:" in settled.stdout

    for req, _ in open_items:
        short = f"R-{req.req_id[:8]}"
        assert short in settled.stdout
        block = settled.stdout.split(short, 1)[1].split("\nR-", 1)[0]
        plan, _error = cli._plan_attest(req, loaded.symbols)
        if plan is None:
            assert "manual attest required" in block
        else:
            assert "proposed" in block


def test_ff_attest_is_not_laundering(tmp_path: Path) -> None:
    """An attested requirement reports its act and reason, never a clean blank."""
    import intentrace.cli as cli
    from intentrace.decisions import judge_requirement

    loaded = _loaded_states(all_states_repo(tmp_path))
    reasons: set[str] = set()
    saw_unattested = False
    for req in loaded.requirements:
        if req.maturity != "active" or req.ratification is None:
            continue
        text = cli._format_requirement(
            req,
            cli._observation_for(loaded.store, req),
            judge_requirement(req, loaded.symbols),
        )
        assert text.strip()
        if not req.ratification.attestations:
            saw_unattested = True
            assert "attested" not in text
            continue
        last = req.ratification.attestations[-1]
        assert "attested" in text
        assert last.reason in text
        assert str(len(req.ratification.attestations)) in text
        reasons.add(last.reason)
        if last.reason == "drift-accepted":
            assert last.rationale
            assert last.rationale in text
    assert reasons == {"late-anchor", "rename", "move", "drift-accepted"}
    assert saw_unattested


# 4. No read path may order by timestamp.


def test_no_read_path_orders_by_timestamp() -> None:
    """A lexical guard over the replay modules, honest about its reach.

    It catches the exact defect this pass removed: a sort or comparison keyed
    on a timestamp in log.py or decisions.py. Display formatting in cli.py is
    deliberately outside the check. It is a guard, not a proof.
    """
    import re

    modules = Path(__file__).resolve().parents[1] / "src" / "intentrace"
    patterns = [
        r"\.sort\([^\n]*timestamp",
        r"sorted\([^\n]*timestamp",
        r"key=[^\n]*timestamp",
        r"timestamp\s*[<>]=?",
        r"[<>]=?\s*[^\n]*\.timestamp",
    ]
    for name in ("log.py", "decisions.py"):
        source = (modules / name).read_text()
        assert source.strip()
        for pattern in patterns:
            match = re.search(pattern, source)
            assert match is None, f"{name} orders by timestamp: {match.group(0)!r}"


# 5. Attestation history is visible, and earlier rationales stay reachable.


def test_why_shows_attestation_count_and_history(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, CODE_EMPTY)
    req_id = derive(repo)[0].req_id
    assert run_cli(repo, "ratify", req_id, "--actor", "tester").returncode == 0

    (repo / "app.py").write_text(CODE_FROB)
    late = run_cli(repo, "attest", req_id, "--actor", "tester")
    assert late.returncode == 0, late.stderr

    (repo / "app.py").write_text(CODE_FROB_CHANGED)
    first = run_cli(
        repo,
        "attest",
        req_id,
        "--actor",
        "tester",
        "--rationale",
        "first drift is acceptable",
    )
    assert first.returncode == 0, first.stderr

    (repo / "app.py").write_text(CODE_FROB_CHANGED_AGAIN)
    second = run_cli(
        repo,
        "attest",
        req_id,
        "--actor",
        "tester",
        "--rationale",
        "second drift is acceptable",
    )
    assert second.returncode == 0, second.stderr

    summary = why(repo)
    assert summary.returncode == 0, summary.stderr
    assert "attested   3 times" in summary.stdout
    assert "drift-accepted" in summary.stdout
    assert "second drift is acceptable" in summary.stdout
    assert "first drift is acceptable" not in summary.stdout

    history = why(repo, "app.py:5", "--history")
    assert history.returncode == 0, history.stderr
    assert "history" in history.stdout
    assert "late-anchor" in history.stdout
    assert "first drift is acceptable" in history.stdout
    assert "second drift is acceptable" in history.stdout
