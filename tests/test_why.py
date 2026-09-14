"""Tests for the `intentrace why` command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Set up a temporary repo with the fixture project and log."""
    import json

    from intentrace.log import append_observation
    from intentrace.models import Observation

    # Create .intentrace directory
    (tmp_path / ".intentrace").mkdir()

    # Copy fixture files
    import shutil

    for f in FIXTURE_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp_path / f.name)

    # Ingest the fixture observations
    obs_file = FIXTURE_DIR / "observations.jsonl"
    with open(obs_file) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            obs = Observation.model_validate(data)
            append_observation(tmp_path, obs)

    return tmp_path


def test_why_prints_covering_requirement(repo_root: Path) -> None:
    """why prints the covering requirement for a line inside an anchored function."""
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "retry.py:11"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    # Must produce a populated block first
    assert "R-" in result.stdout, "expected a populated requirement block"
    assert "sketch" in result.stdout
    assert "unverified" in result.stdout


def test_why_no_intent_covers(repo_root: Path) -> None:
    """why prints 'no intent covers this code' for unanchored code."""
    # Create a file with no matching requirements
    (repo_root / "uncovered.py").write_text("x = 1\n")
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "uncovered.py:1"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    assert "no intent covers this code" in result.stdout


def test_why_never_prints_satisfied(repo_root: Path) -> None:
    """why never prints 'ratified' with a value, 'active', or 'satisfied'."""
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "retry.py:11"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    # Must produce a populated block first
    assert "R-" in result.stdout, "expected a populated requirement block"
    assert "satisfied" not in result.stdout.lower()
    assert "active" not in result.stdout.lower()
    # ratified should only appear as the dash (—)
    for line in result.stdout.splitlines():
        if "ratified" in line.lower():
            assert "—" in line or "ratified   \u2014" in line


def test_why_file_not_found() -> None:
    """why returns non-zero for missing file."""
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "nonexistent.py:1"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    assert result.returncode != 0


def test_why_evidence_none(repo_root: Path) -> None:
    """why prints evidence as none for requirements with no evidence."""
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "retry.py:11"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    # Must produce a populated block first
    assert "R-" in result.stdout, "expected a populated requirement block"
    assert "evidence" in result.stdout
    assert "none" in result.stdout


def test_why_anchor_populated(repo_root: Path) -> None:
    """A requirement anchored by the extractor reports anchors in why output."""
    result = subprocess.run(
        [sys.executable, "-m", "intentrace.cli", "why", "retry.py:11"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    assert "R-" in result.stdout, "expected a populated requirement block"
    # The anchors line must be populated, not 'none'
    assert "anchors    none" not in result.stdout
    assert "anchors    " in result.stdout
    assert "retry.py::RetryPolicy::attempt" in result.stdout


def test_node_hash_changes_on_body_edit(repo_root: Path) -> None:
    """node_hash stored on the requirement differs from fresh hash after body change.

    This verifies the hash *function* is sensitive to behaviour changes.
    System-level drift detection requires persistence (slice 2).
    """
    import json

    from intentrace.anchor.python import build_anchor, parse_source
    from intentrace.extract.fake import FakeExtractor
    from intentrace.models import Observation

    # Load observations and extract with original source
    obs_file = FIXTURE_DIR / "observations.jsonl"
    observations: list[Observation] = []
    with open(obs_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            observations.append(Observation.model_validate(data))

    source_original = (repo_root / "retry.py").read_bytes()
    tree_original = parse_source(source_original)
    original_anchor = build_anchor("retry.py", tree_original, source_original, "attempt")
    assert original_anchor is not None
    original_hash = original_anchor.node_hash

    # Extract with the symbol table
    from intentrace.symbol import SymbolTable

    table = SymbolTable()
    table.by_qualified[original_anchor.symbol_path] = original_anchor
    bare = original_anchor.symbol_path.rsplit("::", 1)[-1]
    table.by_bare[bare] = [original_anchor]
    extractor = FakeExtractor()
    reqs = extractor.extract(observations, symbols=table)

    # Find a requirement anchored to attempt
    anchored = [
        r for r in reqs.requirements if any(a.symbol_path.endswith("attempt") for a in r.anchors)
    ]
    assert len(anchored) > 0, "expected at least one requirement anchored to attempt"
    stored_hash = anchored[0].anchors[0].node_hash
    assert stored_hash == original_hash

    # Now change the function body (add a literal change)
    modified_source = source_original.replace(
        b"return func(*args)", b"return func(*args, retry=True)"
    )
    tree_modified = parse_source(modified_source)
    modified_anchor = build_anchor("retry.py", tree_modified, modified_source, "attempt")
    assert modified_anchor is not None
    new_hash = modified_anchor.node_hash

    # The stored hash must differ from the new hash — this is drift detection
    assert stored_hash != new_hash, (
        "node_hash must change when function body changes — drift detection is broken"
    )
