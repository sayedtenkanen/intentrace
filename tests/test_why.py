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
    assert "R-" in result.stdout
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
        [sys.executable, "-m", "intentrace.cli", "why", "retry.py:7"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
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
    assert "evidence" in result.stdout
    assert "none" in result.stdout
