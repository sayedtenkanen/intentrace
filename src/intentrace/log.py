"""Append-only JSONL observation log with torn-line detection.

Path: .intentrace/log.jsonl at repo root.
One complete JSON object per line, append-only, never rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from intentrace.models import Observation


class TornLineError(Exception):
    """A trailing incomplete line was detected (recoverable)."""

    def __init__(self, line_number: int, raw: str) -> None:
        self.line_number = line_number
        self.raw = raw
        super().__init__(f"torn line {line_number}: {raw!r}")


class CorruptLineError(Exception):
    """A mid-file unparseable line was detected (corruption)."""

    def __init__(self, line_number: int, raw: str) -> None:
        self.line_number = line_number
        self.raw = raw
        super().__init__(f"corrupt line {line_number}: {raw!r}")


@dataclass(frozen=True)
class LogReadResult:
    """Result of reading the log."""

    observations: list[Observation]
    torn_line: TornLineError | None = None


def _log_path(repo_root: Path) -> Path:
    return repo_root / ".intentrace" / "log.jsonl"


def append_observation(repo_root: Path, obs: Observation) -> None:
    """Append a single observation to the log."""
    log_file = _log_path(repo_root)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(obs.model_dump_json() + "\n")


def read_observations(repo_root: Path) -> LogReadResult:
    """Read all observations from the log.

    Returns a LogReadResult with a list of observations and
    optionally a TornLineError if the final line was incomplete.
    """
    log_file = _log_path(repo_root)
    if not log_file.exists():
        return LogReadResult(observations=[])

    with open(log_file, encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return LogReadResult(observations=[])

    torn_error: TornLineError | None = None

    # Check for torn final line
    last_line = lines[-1]
    if last_line.strip():
        try:
            json.loads(last_line)
        except json.JSONDecodeError:
            torn_error = TornLineError(len(lines), last_line.rstrip("\n"))
            lines = lines[:-1]

    observations: list[Observation] = []
    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise CorruptLineError(i + 1, line.rstrip("\n")) from e
        observations.append(Observation.model_validate(data))

    return LogReadResult(observations=observations, torn_line=torn_error)
