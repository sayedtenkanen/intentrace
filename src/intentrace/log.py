"""Append-only JSONL log in one file, read in append order.

Path: .intentrace/log.jsonl at repo root. Observations and decisions of every
kind share it. One complete JSON object per line, append-only, never rewritten.
Position in this file is the only order; a timestamp is display data.

A build before this one wrote decision kinds an older binary could not parse to
a companion `.intentrace/decisions-v2.jsonl`. That file is folded into the base
log once, under the lock, and kept beside it as
`.intentrace/decisions-v2.folded.jsonl`.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from collections import defaultdict
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


class FoldError(Exception):
    """The companion log cannot be folded without guessing.

    The companion is only folded when the order of decisions for every
    requirement it shares with the base log is known. Otherwise the caller
    stops and names the requirement; position in the merged log is never
    invented.
    """

    def __init__(self, message: str, req_id: str | None = None) -> None:
        self.req_id = req_id
        super().__init__(message)


@dataclass(frozen=True)
class LogReadResult:
    """Result of reading the log: entries plus any line-level errors."""

    observations: list[Observation]
    decisions: list[Decision] = field(default_factory=list)
    torn_line: TornLineError | None = None
    corrupt_line: CorruptLineError | None = None


def _log_path(repo_root: Path) -> Path:
    return repo_root / ".intentrace" / "log.jsonl"


def _companion_log_path(repo_root: Path) -> Path:
    """The pre-remediation companion log, if a repository still has one."""
    return repo_root / ".intentrace" / "decisions-v2.jsonl"


def _folded_log_path(repo_root: Path) -> Path:
    """Where the folded companion is kept, unchanged."""
    return repo_root / ".intentrace" / "decisions-v2.folded.jsonl"


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
    """Append a single decision to the base log (immutable; never rewritten).

    Every decision kind shares the one log. Its position is the only record of
    when it happened; the timestamp is display data.
    """
    _append_line(repo_root, decision.model_dump_json() + "\n", _log_path(repo_root))


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


def _check_fold_order(
    base_entries: list[Observation | Decision],
    companion_entries: list[Observation | Decision],
) -> None:
    """Refuse a fold whose per-requirement order would have to be guessed.

    Within each file the append order is known. Across the two files the only
    order this build can establish without reading timestamps is the one the
    writing rules force: a requirement's ratify decisions were written to the
    base log before its attestations were written to the companion, and no
    other decision kind could be interleaved. Anything else stops the fold
    and names the requirement.
    """
    base_by_req: dict[str, list[Decision]] = defaultdict(list)
    companion_by_req: dict[str, list[Decision]] = defaultdict(list)
    for entry in base_entries:
        if isinstance(entry, Decision):
            base_by_req[entry.req_id].append(entry)
    for entry in companion_entries:
        if isinstance(entry, Decision):
            companion_by_req[entry.req_id].append(entry)

    for req_id, companion_records in companion_by_req.items():
        base_records = base_by_req.get(req_id)
        if base_records is None:
            # Nothing in the base log competes for this requirement's order.
            continue
        if any(record.kind != "attest" for record in companion_records):
            raise FoldError(
                f"cannot fold the companion log: decision order for requirement "
                f"{req_id} cannot be established (the companion holds a "
                f"non-attestation after a base-log decision)",
                req_id=req_id,
            )
        if any(record.kind != "ratify" for record in base_records):
            raise FoldError(
                f"cannot fold the companion log: decision order for requirement "
                f"{req_id} cannot be established (the base log holds a "
                f"non-ratification after which an attestation was appended)",
                req_id=req_id,
            )


def _fold_companion_log(repo_root: Path) -> None:
    """Fold a pre-remediation companion log into the base log, once.

    The companion's complete records are appended in their written order and
    the original file is renamed, byte for byte, to a name that says it was
    folded. A partial trailing record is left in the folded file, not silently
    discarded, and reported. If order cannot be established the fold stops
    before modifying either file.
    """
    companion = _companion_log_path(repo_root)
    if not companion.exists():
        return

    with log_lock(repo_root):
        if not companion.exists():
            return
        folded = _folded_log_path(repo_root)
        if folded.exists():
            raise FoldError(
                f"cannot fold {companion.name}: {folded.name} already exists; "
                "refusing to overwrite a previous fold"
            )

        companion_entries, torn_error, corrupt_error = _parse_log_file(companion)
        if corrupt_error is not None:
            raise FoldError(f"cannot fold {companion.name}: {corrupt_error}")
        base_entries, _, base_corrupt = _parse_log_file(_log_path(repo_root))
        if base_corrupt is not None:
            # The read will report the base log's corruption. Leave the
            # companion untouched so the fold can be retried after repair.
            return

        _check_fold_order(base_entries, companion_entries)
        # Append the companion's complete lines byte for byte. Re-serializing
        # would be a rewrite of records the fold promises to keep unchanged.
        companion_lines = _iter_lines(companion)
        complete_lines = companion_lines[:-1] if torn_error is not None else companion_lines
        if complete_lines:
            _append_line(repo_root, "".join(complete_lines), _log_path(repo_root))

        if torn_error is not None:
            print(
                f"warning: {torn_error}; the partial record is kept in {folded.name}",
                file=sys.stderr,
            )
        companion.rename(folded)
        print(
            f"folded {companion.name} into {_log_path(repo_root).name}; "
            f"original kept as {folded.name}",
            file=sys.stderr,
        )


def read_observations(repo_root: Path) -> LogReadResult:
    """Read observations and decisions from the log, in append order.

    Named for its original Slice 1 role; it returns the full log envelope
    (observations and decisions) and the name is kept stable for callers.
    Entries are returned in the order they were appended. Timestamps are
    display data: no read path sorts or compares them. A timestamp with no
    timezone is returned exactly as it was stored.

    A pre-remediation companion log is folded first, once. A trailing
    unparseable line is detected as a torn line (recoverable). A mid-file
    unparseable line is corruption. Lines carrying dec_id are decisions;
    lines carrying obs_id are observations; anything else is corruption.
    """
    _fold_companion_log(repo_root)
    entries, torn_error, corrupt_error = _parse_log_file(_log_path(repo_root))
    observations = [entry for entry in entries if isinstance(entry, Observation)]
    decisions = [entry for entry in entries if isinstance(entry, Decision)]

    return LogReadResult(
        observations=observations,
        decisions=decisions,
        torn_line=torn_error,
        corrupt_line=corrupt_error,
    )
