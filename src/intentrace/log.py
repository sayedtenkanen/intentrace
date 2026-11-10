"""Append-only JSONL log family with torn-line detection.

Base path: .intentrace/log.jsonl at repo root. It carries observations and
decision kinds an older build can parse.
Versioned path: .intentrace/decisions-v2.jsonl. It carries decision kinds an
older build cannot parse (currently `attest`), so a rollback still reads the
base log normally.
One complete JSON object per line, append-only, never rewritten.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

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


def _versioned_log_path(repo_root: Path) -> Path:
    """Companion log for decision kinds an older build cannot parse."""
    return repo_root / ".intentrace" / "decisions-v2.jsonl"


# The decision kinds a pre-slice-4 binary accepts (its `kind` is a Literal).
# Records of these kinds stay in the base log so a rollback can still read
# the repository. Anything else goes to the versioned companion log.
LEGACY_DECISION_KINDS = frozenset({"ratify", "waive", "demote", "archive", "migrate"})


def _decision_log_path(repo_root: Path, decision: Decision) -> Path:
    if decision.kind in LEGACY_DECISION_KINDS:
        return _log_path(repo_root)
    return _versioned_log_path(repo_root)


_lock_state = threading.local()


@contextmanager
def log_lock(repo_root: Path) -> Iterator[None]:
    """Serialize appends and check-then-append sequences across processes.

    A lock file beside the log carries an advisory exclusive lock. The lock
    is re-entrant within a thread, so a caller can hold it across a re-read
    and still call append_observation/append_decision. Platforms without
    fcntl/msvcrt fall back to in-process serialization only.
    """
    depth = getattr(_lock_state, "depth", 0)
    if depth:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return

    lock_path = repo_root / ".intentrace" / "log.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        _acquire_lock(lock_file)
        _lock_state.depth = 1
        try:
            yield
        finally:
            _lock_state.depth = 0
            _release_lock(lock_file)


def _acquire_lock(lock_file: BinaryIO) -> None:
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return
    try:
        fcntl = importlib.import_module("fcntl")
    except ImportError:  # pragma: no cover - non-POSIX, non-Windows
        return
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_lock(lock_file: BinaryIO) -> None:
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    try:
        fcntl = importlib.import_module("fcntl")
    except ImportError:  # pragma: no cover - non-POSIX, non-Windows
        return
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _truncate_torn_tail(log_file: Path) -> None:
    """Drop a final record that was not terminated by a newline.

    An interrupted append leaves partial bytes at the end. Appending after
    those bytes would make a syntactically complete but invalid record, which
    the reader reports as corruption. Truncating the partial record first
    keeps the next append readable.
    """
    if not log_file.exists():
        return
    data = log_file.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    cut = data.rfind(b"\n") + 1
    with open(log_file, "r+b") as f:
        f.truncate(cut)


def _append_line(repo_root: Path, line: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with log_lock(repo_root):
        _truncate_torn_tail(target)
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)


def append_observation(repo_root: Path, obs: Observation) -> None:
    """Append a single observation to the base log."""
    _append_line(repo_root, obs.model_dump_json() + "\n", _log_path(repo_root))


def append_decision(repo_root: Path, decision: Decision) -> None:
    """Append a single decision to the log (immutable; never rewritten).

    A kind an older binary cannot parse is written to the versioned
    companion log, so the base log remains readable after a rollback.
    """
    _append_line(
        repo_root,
        decision.model_dump_json() + "\n",
        _decision_log_path(repo_root, decision),
    )


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


def _parse_log_file(
    log_file: Path,
) -> tuple[list[Observation | Decision], TornLineError | None, CorruptLineError | None]:
    """Parse one JSONL file, reporting a torn or corrupt line."""
    if not log_file.exists():
        return [], None, None

    lines = _iter_lines(log_file)
    if not lines:
        return [], None, None

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

    entries: list[Observation | Decision] = []

    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
            break
        try:
            if isinstance(data, dict) and "dec_id" in data:
                entries.append(Decision.model_validate(data))
            elif isinstance(data, dict) and "obs_id" in data:
                entries.append(Observation.model_validate(data))
            else:
                corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
                break
        except Exception:
            corrupt_error = CorruptLineError(i + 1, line.rstrip("\n"))
            break

    return entries, torn_error, corrupt_error


def read_observations(repo_root: Path) -> LogReadResult:
    """Read all observations and decisions from the log family.

    Named for its original Slice 1 role; it returns the full log envelope
    (observations and decisions) and the name is kept stable for callers.
    The base log carries observations and decision kinds a pre-slice-4
    binary can parse. The versioned companion log carries newer decision
    kinds. Entries from both are merged by timestamp, so an attestation
    still follows the ratification it amends.

    A trailing unparseable line is detected as a torn line (recoverable).
    A mid-file unparseable line is corruption. Lines carrying dec_id are
    decisions; lines carrying obs_id are observations; anything else is
    corruption.
    """
    base_entries, torn_error, corrupt_error = _parse_log_file(_log_path(repo_root))
    extra_entries, extra_torn, extra_corrupt = _parse_log_file(_versioned_log_path(repo_root))
    torn_error = torn_error or extra_torn
    corrupt_error = corrupt_error or extra_corrupt

    # Base first on a timestamp tie: an attestation cannot precede the
    # ratification it amends, and the base holds the ratification.
    ranked = [
        *((entry.timestamp, 0, index, entry) for index, entry in enumerate(base_entries)),
        *((entry.timestamp, 1, index, entry) for index, entry in enumerate(extra_entries)),
    ]
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))

    observations = [entry for _, _, _, entry in ranked if isinstance(entry, Observation)]
    decisions = [entry for _, _, _, entry in ranked if isinstance(entry, Decision)]

    return LogReadResult(
        observations=observations,
        decisions=decisions,
        torn_line=torn_error,
        corrupt_line=corrupt_error,
    )
