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
from intentrace.models import Decision, Observation, RequirementSnapshot, Span

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


# 6. Same words, different code: show it, do not merge it.


def _anchor(file: str, name: str, node_hash: str):
    from intentrace.models import AnchorRef

    return AnchorRef(
        lang="python",
        file=file,
        symbol_path=f"{file}::{name}",
        node_kind="function_definition",
        node_hash=node_hash,
        name_hash=node_hash,
    )


def test_same_words_different_code_is_shown_not_merged() -> None:
    """The snapshot's anchors are left alone; the sketch stays for a decision."""
    from intentrace.decisions import apply_decisions
    from intentrace.models import Requirement
    from intentrace.symbol import SymbolTable

    statement = FROB
    anchor_a = _anchor("app.py", "frobnicate", "1" * 64)
    anchor_b = _anchor("other.py", "frobnicate", "2" * 64)
    req_a = Requirement.create(
        statement,
        [Span(obs_id="a" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_a],
    )
    req_b = Requirement.create(
        statement,
        [Span(obs_id="b" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_b],
    )
    ratify = Decision.create(
        kind="ratify",
        req_id=req_a.req_id,
        actor="tester",
        baseline_hashes={anchor_a.symbol_path: anchor_a.node_hash},
        baseline_name_hashes={anchor_a.symbol_path: anchor_a.name_hash},
        snapshot=RequirementSnapshot(
            statement=statement,
            provenance=req_a.provenance,
            extractor_version="fake-v2",
        ),
    )
    symbols = SymbolTable(
        by_qualified={anchor_a.symbol_path: anchor_a, anchor_b.symbol_path: anchor_b}
    )

    view = apply_decisions([req_a, req_b], [ratify], symbols)

    active = [r for r in view.requirements if r.maturity == "active"]
    assert len(active) == 1
    assert {a.symbol_path for a in active[0].anchors} == {anchor_a.symbol_path}
    attached = [a for a in view.attached if a.sketch.req_id == req_b.req_id]
    assert len(attached) == 1
    assert attached[0].reason == "same words, different code"
    assert attached[0].snapshot_req_id == req_a.req_id
    assert any(r.maturity == "sketch" and r.req_id == req_b.req_id for r in view.requirements)
    assert all(s.sketch.req_id != req_b.req_id for s in view.suppressed)


def test_duplicate_statement_with_a_new_anchor_can_be_ratified(tmp_path: Path) -> None:
    """A same-words sketch proposing different code is a separate obligation."""
    import intentrace.cli as cli

    (tmp_path / ".intentrace").mkdir()
    statement = FROB
    existing = Decision.create(
        kind="ratify",
        req_id="a" * 64,
        actor="tester",
        baseline_hashes={"app.py::frobnicate": "1" * 64},
        snapshot=RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id="a" * 64, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )
    append_decision(tmp_path, existing)

    decision = cli._record_ratification(
        tmp_path,
        "b" * 64,
        "tester",
        "",
        {"other.py::frobnicate": "2" * 64},
        {"other.py::frobnicate": "2" * 64},
        RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id="b" * 64, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )

    assert decision.decision is not None
    assert [d.req_id for d in read_observations(tmp_path).decisions] == [
        "a" * 64,
        "b" * 64,
    ]


def test_duplicate_statement_already_covered_is_still_refused(tmp_path: Path) -> None:
    """Dedup still wins when the existing effective baseline covers the candidate."""
    import intentrace.cli as cli

    (tmp_path / ".intentrace").mkdir()
    statement = FROB
    existing = Decision.create(
        kind="ratify",
        req_id="a" * 64,
        actor="tester",
        baseline_hashes={"app.py::frobnicate": "1" * 64},
        snapshot=RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id="a" * 64, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )
    covered = Decision.create(
        kind="attest",
        req_id="a" * 64,
        actor="tester",
        reason="late-anchor",
        baseline_hashes={
            "app.py::frobnicate": "1" * 64,
            "other.py::frobnicate": "2" * 64,
        },
    )
    append_decision(tmp_path, existing)
    append_decision(tmp_path, covered)

    decision = cli._record_ratification(
        tmp_path,
        "b" * 64,
        "tester",
        "",
        {"other.py::frobnicate": "2" * 64},
        {"other.py::frobnicate": "2" * 64},
        RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id="b" * 64, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )

    assert decision.decision is None
    assert decision.refusal == "covered-by-another"
    assert len(read_observations(tmp_path).decisions) == 2


# 7. Coverage is checked against every same-statement requirement, not the
#    first match, and never against anchors that are only pending.


def _ratify_decision(
    req_id: str,
    statement: str,
    obs_id: str,
    baseline: dict[str, str],
) -> Decision:
    return Decision.create(
        kind="ratify",
        req_id=req_id,
        actor="tester",
        baseline_hashes=baseline,
        baseline_name_hashes=baseline,
        snapshot=RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id=obs_id, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )


def test_view_checks_every_same_statement_requirement_for_coverage() -> None:
    """A sketch covered by the second match is suppressed, not queued."""
    from intentrace.decisions import apply_decisions
    from intentrace.models import Requirement
    from intentrace.symbol import SymbolTable

    statement = FROB
    anchor_a = _anchor("app.py", "alpha", "1" * 64)
    anchor_b = _anchor("app.py", "beta", "2" * 64)
    req_a = Requirement.create(
        statement, [Span(obs_id="a" * 64, start=0, end=len(statement))], "fake-v2"
    )
    req_b = Requirement.create(
        statement, [Span(obs_id="b" * 64, start=0, end=len(statement))], "fake-v2"
    )
    sketch = Requirement.create(
        statement,
        [Span(obs_id="c" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_b],
    )
    decisions = [
        _ratify_decision(
            req_a.req_id, statement, "a" * 64, {anchor_a.symbol_path: anchor_a.node_hash}
        ),
        _ratify_decision(
            req_b.req_id, statement, "b" * 64, {anchor_b.symbol_path: anchor_b.node_hash}
        ),
    ]
    symbols = SymbolTable(
        by_qualified={anchor_a.symbol_path: anchor_a, anchor_b.symbol_path: anchor_b}
    )

    view = apply_decisions([sketch], decisions, symbols)

    assert view.attached == []
    assert [s.sketch.req_id for s in view.suppressed] == [sketch.req_id]
    assert view.suppressed[0].snapshot_req_id == req_b.req_id
    assert view.suppressed[0].reason == "same statement"
    assert all(r.req_id != sketch.req_id for r in view.requirements)


def test_pending_anchor_does_not_hide_a_later_separate_proposal() -> None:
    """A pending anchor is display state, not coverage for a second sketch."""
    from intentrace.decisions import apply_decisions
    from intentrace.models import Requirement
    from intentrace.symbol import SymbolTable

    statement = FROB
    anchor_a = _anchor("app.py", "alpha", "1" * 64)
    anchor_b = _anchor("app.py", "beta", "2" * 64)
    active_span = Span(obs_id="a" * 64, start=0, end=len(statement))
    req_a = Requirement.create(statement, [active_span], "fake-v2")
    # Same utterance, re-derived with a new anchor: b is pending on A.
    same_span = Requirement.create(
        statement, [active_span], "fake-v2", anchors=[anchor_a, anchor_b]
    )
    # A different observation with the same words also proposes b.
    other_observation = Requirement.create(
        statement,
        [Span(obs_id="b" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_b],
    )
    ratify = _ratify_decision(
        req_a.req_id, statement, "a" * 64, {anchor_a.symbol_path: anchor_a.node_hash}
    )
    symbols = SymbolTable(
        by_qualified={anchor_a.symbol_path: anchor_a, anchor_b.symbol_path: anchor_b}
    )

    view = apply_decisions([same_span, other_observation], [ratify], symbols)

    active = next(r for r in view.requirements if r.req_id == req_a.req_id)
    assert {a.symbol_path for a in active.anchors} == {
        anchor_a.symbol_path,
        anchor_b.symbol_path,
    }
    assert set(active.ratification.baseline_hashes) == {anchor_a.symbol_path}  # type: ignore[union-attr]
    attached = [a for a in view.attached if a.sketch.req_id == other_observation.req_id]
    assert len(attached) == 1
    assert attached[0].reason == "same words, different code"
    assert any(
        r.maturity == "sketch" and r.req_id == other_observation.req_id for r in view.requirements
    )


def test_ratification_refuses_anchors_covered_by_a_union_of_baselines(tmp_path: Path) -> None:
    """Collective coverage is still coverage; no duplicate obligation."""
    import intentrace.cli as cli
    from intentrace.models import Requirement

    (tmp_path / ".intentrace").mkdir()
    statement = FROB
    anchor_a = _anchor("app.py", "alpha", "1" * 64)
    anchor_b = _anchor("app.py", "beta", "2" * 64)
    req_a = Requirement.create(
        statement, [Span(obs_id="a" * 64, start=0, end=len(statement))], "fake-v2"
    )
    req_b = Requirement.create(
        statement, [Span(obs_id="b" * 64, start=0, end=len(statement))], "fake-v2"
    )
    append_decision(
        tmp_path,
        _ratify_decision(
            req_a.req_id, statement, "a" * 64, {anchor_a.symbol_path: anchor_a.node_hash}
        ),
    )
    append_decision(
        tmp_path,
        _ratify_decision(
            req_b.req_id, statement, "b" * 64, {anchor_b.symbol_path: anchor_b.node_hash}
        ),
    )
    before = len(read_observations(tmp_path).decisions)

    result = cli._record_ratification(
        tmp_path,
        "c" * 64,
        "tester",
        "",
        {anchor_a.symbol_path: anchor_a.node_hash, anchor_b.symbol_path: anchor_b.node_hash},
        {},
        RequirementSnapshot(
            statement=statement,
            provenance=[Span(obs_id="c" * 64, start=0, end=len(statement))],
            extractor_version="fake-v2",
        ),
    )

    assert result.decision is None
    assert result.refusal == "covered-by-another"
    assert len(read_observations(tmp_path).decisions) == before


def test_ratify_reports_coverage_by_another_requirement(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A concurrent same-statement ratification is not reported as this sketch being active."""
    import argparse
    from pathlib import Path as _Path

    import intentrace.cli as cli
    from intentrace.extract.port import ExtractionResult
    from intentrace.models import Requirement
    from intentrace.store import MemoryStore
    from intentrace.symbol import SymbolTable

    statement = FROB
    anchor_a = _anchor("app.py", "alpha", "1" * 64)
    anchor_b = _anchor("app.py", "beta", "2" * 64)
    sketch = Requirement.create(
        statement,
        [Span(obs_id="b" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_b],
    )
    symbols = SymbolTable(
        by_qualified={anchor_a.symbol_path: anchor_a, anchor_b.symbol_path: anchor_b}
    )
    # A different requirement with the same statement already covers the
    # anchor the candidate proposes.
    append_decision(
        tmp_path,
        _ratify_decision(
            "a" * 64,
            statement,
            "a" * 64,
            {anchor_b.symbol_path: anchor_b.node_hash},
        ),
    )
    view = cli._View(
        repo_root=tmp_path,
        store=MemoryStore(tmp_path),
        requirements=[sketch],
        orphaned=[],
        suppressed=[],
        attached=[],
        unknown=[],
        symbols=symbols,
    )

    class StubExtractor:
        version = "fake-v2"

        def extract(self, observations, symbols=None):  # type: ignore[no-untyped-def]
            return ExtractionResult(requirements=[sketch])

    monkeypatch.setattr(cli, "_find_repo_root", lambda: _Path(tmp_path))
    monkeypatch.setattr(cli, "_load_view", lambda _root: view)
    monkeypatch.setattr(cli, "build_symbol_table", lambda _root: symbols)
    monkeypatch.setattr(cli, "FakeExtractor", StubExtractor)
    monkeypatch.setattr(cli, "_ask", lambda _prompt: "y")

    rc = cli.cmd_ratify(argparse.Namespace(req_id=sketch.req_id, actor="tester"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "covered by another requirement's ratification" in out
    assert "is already active" not in out


def test_settle_presents_uncovered_same_statement_sketch_after_ratifying_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A same-statement sketch with an uncovered anchor is not skipped by text alone."""
    import argparse
    from pathlib import Path as _Path

    import intentrace.cli as cli
    from intentrace.extract.port import ExtractionResult
    from intentrace.models import Requirement
    from intentrace.symbol import SymbolTable

    statement = FROB
    anchor_a = _anchor("app.py", "alpha", "1" * 64)
    anchor_b = _anchor("app.py", "beta", "2" * 64)
    anchor_c = _anchor("app.py", "gamma", "3" * 64)
    req_a = Requirement.create(
        statement, [Span(obs_id="a" * 64, start=0, end=len(statement))], "fake-v2"
    )
    append_decision(
        tmp_path,
        _ratify_decision(
            req_a.req_id, statement, "a" * 64, {anchor_a.symbol_path: anchor_a.node_hash}
        ),
    )
    first = Requirement.create(
        statement,
        [Span(obs_id="b" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_a, anchor_b],
    )
    second = Requirement.create(
        statement,
        [Span(obs_id="c" * 64, start=0, end=len(statement))],
        "fake-v2",
        anchors=[anchor_a, anchor_b, anchor_c],
    )
    symbols = SymbolTable(
        by_qualified={
            anchor_a.symbol_path: anchor_a,
            anchor_b.symbol_path: anchor_b,
            anchor_c.symbol_path: anchor_c,
        }
    )

    class StubExtractor:
        version = "fake-v2"

        def extract(self, observations, symbols=None):  # type: ignore[no-untyped-def]
            return ExtractionResult(requirements=[first, second])

    monkeypatch.setattr(cli, "_find_repo_root", lambda: _Path(tmp_path))
    monkeypatch.setattr(cli, "build_symbol_table", lambda _root: symbols)
    monkeypatch.setattr(cli, "FakeExtractor", StubExtractor)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = cli.cmd_settle(argparse.Namespace(actor="tester"))

    out = capsys.readouterr().out
    assert rc == 0
    assert f"R-{first.req_id[:8]}" in out
    assert f"R-{second.req_id[:8]}" in out
    assert "2 ratified" in out
    assert "skipped" not in out.replace("0 skipped", "")
