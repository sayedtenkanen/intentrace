"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root with .intentrace directory."""
    (tmp_path / ".intentrace").mkdir()
    return tmp_path
