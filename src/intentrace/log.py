"""Append-only JSONL observation log with torn-line detection.

Path: .intentrace/log.jsonl at repo root.
One complete JSON object per line, append-only, never rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from intentrace.models import Decision, Observation


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
    """Result of reading the log: entries plus any line-level errors."""

    observations: list[Observation]
    decisions: list[Decision] = field(default_factory=list)
    torn_line: TornLineError | None = None
    corrupt_line: CorruptLineError | None = None


def _log_path(repo_root: Path) -> Path:
    return repo_root / ".intentrace" / "log.jsonl"


def append_observation(repo_root: Path, obs: Observation) -> None:
    """Append a single observation to the log."""
    log_file = _log_path(repo_root)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(obs.model_dump_json() + "\n")


def append_decision(repo_root: Path, decision: Decision) -> None:
    """Append a single decision to the log (immutable; never rewritten)."""
    log_file = _log_path(repo_root)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(decision.model_dump_json() + "\n")


def _iter_lines(log_file: Path) -> list[str]:
    """Read all lines from the log file.

    Returns the lines as a list. The log is small in v1; streaming was
    deferred. Torn-line detection requires inspecting the final line,
    which a generator cannot do without consuming the entire stream.
    """
    lines: list[str] = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            lines.append(line)
    return lines


def read_observations(repo_root: Path) -> LogReadResult:
    """Read all observations and decisions from the log.

    Reads all lines and returns them as lists. A trailing unparseable
    line is detected as a torn line (recoverable). A mid-file unparseable
    line is corruption. Lines carrying dec_id are decisions; lines carrying
    obs_id are observations; anything else is corruption.
    """
    log_file = _log_path(repo_root)
    if not log_file.exists():
        return LogReadResult(observations=[])

    lines = _iter_lines(log_file)

    if not lines:
        return LogReadResult(observations=[])

    torn_error: TornLineError | None = None
    corrupt_error: CorruptLineError | None = None

    # Check for torn or corrupt final line.
    # A torn line lacks a terminating newline (incomplete append).
    # A corrupt line has a newline but is not valid JSON or fails schema validation.
    last_line = lines[-1]
    if last_line.strip():
        try:
            json.loads(last_line)
        except json.JSONDecodeError:
            if not last_line.endswith("\n"):
                # Truncated — recoverable
                torn_error = TornLineError(len(lines), last_line.rstrip("\n"))
                lines = lines[:-1]
            else:
                # Complete but invalid JSON — corruption
                corrupt_error = CorruptLineError(len(lines), last_line.rstrip("\n"))
                lines = lines[:-1]

    observations: list[Observation] = []
    decisions: list[Decision] = []

    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
            break
        try:
            if isinstance(data, dict) and "dec_id" in data:
                decisions.append(Decision.model_validate(data))
            elif isinstance(data, dict) and "obs_id" in data:
                observations.append(Observation.model_validate(data))
            else:
                corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
                break
        except Exception:
            corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
            break

    return LogReadResult(
        observations=observations,
        decisions=decisions,
        torn_line=torn_error,
        corrupt_line=corrupt_error,
    )
