"""Corpus harvest pipeline (research throwaway, not shipped).

Reads Claude Code session JSONL files READ-ONLY, maps human prompts to
intentrace Observations, runs the real FakeExtractor, and emits aggregate
metrics plus a candidate sample manifest.

Privacy contract (see docs/slices/corpus-harvest.md §0):
- Transcripts are never copied, moved, or rewritten; they are streamed.
- NOTHING containing prompt text is written inside the repository. All
  outputs go to OUT_DIR, which must be outside the repo. The committed
  findings document carries aggregate statistics only.

Usage:
    uv run --extra dev python tools/harvest.py \
        --sessions a.jsonl b.jsonl --out /tmp/harvest-out --seed 20260918
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Reuse intentrace's own code: the Observation model, the extractor, the
# symbol-table builder. A reimplementation would measure the reimplementation.
from intentrace.anchor.python import build_anchor, parse_source
from intentrace.extract.fake import FakeExtractor
from intentrace.models import Observation, _normalize_for_hash
from intentrace.symbol import SymbolTable, build_symbol_table

SAMPLE_TARGET = 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=20260918)
    return parser.parse_args()


def is_prompt(record: dict) -> bool:
    """A human prompt: user record, string content, human origin."""
    if record.get("type") != "user":
        return False
    message = record.get("message", {})
    if not isinstance(message.get("content"), str):
        return False
    origin = record.get("origin", {})
    return isinstance(origin, dict) and origin.get("kind") == "human"


def load_prompts(paths: list[str]) -> tuple[list[dict], dict]:
    """Stream session files; return prompt dicts plus an audit count map."""
    prompts: list[dict] = []
    audit: Counter[str] = Counter()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    audit["unparseable_line"] += 1
                    continue
                if not isinstance(record, dict):
                    audit["non_object_line"] += 1
                    continue
                rtype = record.get("type")
                if rtype != "user":
                    audit[f"skip_type_{rtype}"] += 1
                    continue
                message = record.get("message", {})
                content = message.get("content")
                origin = record.get("origin", {})
                kind = origin.get("kind") if isinstance(origin, dict) else None
                if isinstance(content, str) and kind == "human":
                    prompts.append(record)
                    audit["prompt"] += 1
                elif isinstance(content, str):
                    audit["user_str_nonhuman_origin"] += 1
                else:
                    audit["user_non_string_content"] += 1
    return prompts, {"counts": dict(audit)}


def to_observation(record: dict, turn_index: int) -> Observation:
    """Map one transcript record to the real Observation model."""
    ts = datetime.fromisoformat(record["timestamp"])
    return Observation.create(
        session_id=record.get("sessionId", "unknown"),
        turn_index=turn_index,
        kind="prompt",
        text=record["message"]["content"],
        timestamp=ts,
        source="corpus-harvest",
    )


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; values must be non-empty."""
    ordered = sorted(values)
    rank = min(len(ordered), max(1, math.ceil((pct / 100) * len(ordered))))
    return ordered[rank - 1]


def git_file_revisions(repo: Path, file: str, since: str, limit: int) -> list[tuple[str, str]]:
    """Commits touching file at/after `since` (hash, ISO date), oldest first."""
    out = subprocess.run(
        ["git", "log", "--format=%H %ci", f"--since={since}", "--", file],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if out.returncode != 0:
        return []
    revs = []
    for line in out.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            revs.append((parts[0], parts[1][:10]))
    revs.reverse()
    return revs[:limit]


def hash_at_revision(repo: Path, rev: str, file: str, bare_name: str) -> str | None:
    """node_hash of the symbol called bare_name in file@rev; None if gone."""
    out = subprocess.run(["git", "show", f"{rev}:{file}"], capture_output=True, cwd=repo)
    if out.returncode != 0:
        return None
    try:
        source = out.stdout
        tree = parse_source(source)
    except Exception:
        return None
    anchor = build_anchor(file, tree, source, symbol_name=bare_name)
    return anchor.node_hash if anchor is not None else None


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out)
    if ".git" in [p.name for p in [out_dir, *out_dir.parents]]:
        print("error: OUT_DIR must be outside the repository", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts, audit = load_prompts(args.sessions)

    # Group prompts per session, in transcript order.
    by_session: dict[str, list[dict]] = {}
    for record in prompts:
        by_session.setdefault(record.get("sessionId", "unknown"), []).append(record)

    tables: dict[str, SymbolTable | None] = {}
    candidates: list[dict] = []
    per_prompt: list[dict] = []

    for session_id, records in by_session.items():
        for turn_index, record in enumerate(records):
            obs = to_observation(record, turn_index)
            cwd = record.get("cwd", "")
            if cwd not in tables:
                root = Path(cwd) if cwd else None
                tables[cwd] = (
                    build_symbol_table(root) if root is not None and root.is_dir() else None
                )
            table = tables[cwd]
            result = FakeExtractor().extract([obs], symbols=table)
            per_prompt.append(
                {
                    "session_id": session_id,
                    "cwd": cwd,
                    "n_candidates": len(result.requirements),
                    "n_anchored": sum(1 for r in result.requirements if r.anchors),
                    "ambiguous_names": sorted(result.ambiguous_symbols),
                    "has_table": table is not None,
                }
            )
            for req in result.requirements:
                candidates.append(
                    {
                        "req_id": req.req_id,
                        "session_id": session_id,
                        "prompt_index": turn_index,
                        "statement": req.statement,
                        "normalized": _normalize_for_hash(req.statement),
                        "n_anchors": len(req.anchors),
                        "anchors": [
                            {
                                "file": a.file,
                                "symbol_path": a.symbol_path,
                                "node_hash": a.node_hash,
                            }
                            for a in req.anchors
                        ],
                        "cwd": cwd,
                        "timestamp": record.get("timestamp"),
                    }
                )

    prompts_per_session = [len(v) for v in by_session.values()]
    cands_per_session: Counter[str] = Counter()
    for c in candidates:
        cands_per_session[c["session_id"]] += 1
    cands_session_values = [cands_per_session[s] for s in by_session]
    per_prompt_values = sorted(p["n_candidates"] for p in per_prompt)

    def slug_of(path: str) -> str:
        return (path or "unknown").rstrip("/").rsplit("/", 1)[-1]

    by_project: dict[str, dict[str, int]] = {}
    for p in per_prompt:
        entry = by_project.setdefault(
            slug_of(p["cwd"]), {"prompts": 0, "candidates": 0, "anchored": 0}
        )
        entry["prompts"] += 1
        entry["candidates"] += p["n_candidates"]
    anchored = [c for c in candidates if c["n_anchors"] > 0]
    for c in anchored:
        by_project[slug_of(c["cwd"])]["anchored"] += 1

    norm_counter: Counter[str] = Counter(c["normalized"] for c in candidates)
    norm_sessions: dict[str, set[str]] = {}
    for c in candidates:
        norm_sessions.setdefault(c["normalized"], set()).add(c["session_id"])
    dup_within = sum(
        v - 1
        for v in Counter((c["session_id"], c["normalized"]) for c in candidates).values()
        if v > 1
    )
    dup_cross = sum(1 for s in norm_sessions.values() if len(s) > 1)

    metrics = {
        "corpus": {
            "session_files": args.sessions,
            "n_sessions": len(by_session),
            "timestamp_range": [
                min(r.get("timestamp", "") for r in prompts) if prompts else None,
                max(r.get("timestamp", "") for r in prompts) if prompts else None,
            ],
        },
        "audit": audit,
        "volume": {
            "n_prompts": len(prompts),
            "prompts_per_session": {
                "values": sorted(prompts_per_session),
                "median": statistics.median(prompts_per_session) if prompts_per_session else 0,
                "p90": percentile([float(v) for v in prompts_per_session], 90)
                if prompts_per_session
                else 0,
            },
            "n_candidates": len(candidates),
            "candidates_per_session": {
                "values": sorted(cands_session_values),
                "median": statistics.median(cands_session_values) if cands_session_values else 0,
                "p90": percentile([float(v) for v in cands_session_values], 90)
                if cands_session_values
                else 0,
            },
            "candidates_per_prompt": (len(candidates) / len(per_prompt) if per_prompt else 0),
            "candidates_per_prompt_dist": {
                "values": per_prompt_values,
                "median": statistics.median(per_prompt_values) if per_prompt_values else 0,
                "p90": percentile([float(v) for v in per_prompt_values], 90)
                if per_prompt_values
                else 0,
                "max": max(per_prompt_values) if per_prompt_values else 0,
            },
            "by_project": by_project,
            "share_prompts_with_candidate": (
                sum(1 for p in per_prompt if p["n_candidates"] > 0) / len(per_prompt)
                if per_prompt
                else 0
            ),
        },
        "anchoring": {
            "share_anchored": len(anchored) / len(candidates) if candidates else 0,
            "share_unanchored": (len(candidates) - len(anchored)) / len(candidates)
            if candidates
            else 0,
            "share_prompts_with_ambiguity": (
                sum(1 for p in per_prompt if p["ambiguous_names"]) / len(per_prompt)
                if per_prompt
                else 0
            ),
            "anchors_per_anchored": sorted(c["n_anchors"] for c in anchored),
            "prompts_without_table": sum(1 for p in per_prompt if not p["has_table"]),
        },
        "duplication": {
            "distinct_normalized": len(norm_counter),
            "statements_seen_more_than_once": sum(1 for v in norm_counter.values() if v > 1),
            "extra_within_session_copies": dup_within,
            "statements_spanning_sessions": dup_cross,
        },
    }

    # Decay input: for each distinct anchored symbol, walk history forward
    # from the session date and find the first node-hash change.
    decay: list[dict] = []
    seen_symbols: set[tuple[str, str, str]] = set()
    for c in candidates:
        for a in c["anchors"]:
            key = (c["cwd"], a["file"], a["symbol_path"])
            if key in seen_symbols:
                continue
            seen_symbols.add(key)
            repo = Path(c["cwd"]) if c["cwd"] else None
            if repo is None or not (repo / ".git").exists():
                decay.append({"symbol_path": a["symbol_path"], "status": "untracked"})
                continue
            bare = a["symbol_path"].rsplit("::", 1)[-1]
            revs = git_file_revisions(repo, a["file"], c["timestamp"][:10], 8)
            if not revs:
                gone = subprocess.run(
                    ["git", "cat-file", "-e", f"HEAD:{a['file']}"],
                    capture_output=True,
                    cwd=repo,
                )
                decay.append(
                    {
                        "symbol_path": a["symbol_path"],
                        "status": "no_history_file_gone"
                        if gone.returncode != 0
                        else "no_history_no_commits",
                    }
                )
                continue
            first_hash = hash_at_revision(repo, revs[0][0], a["file"], bare)
            outcome: dict = {
                "symbol_path": a["symbol_path"],
                "status": "censored",
                "since": revs[0][1],
            }
            for rev, date in revs[1:]:
                h = hash_at_revision(repo, rev, a["file"], bare)
                if h is None:
                    outcome = {"symbol_path": a["symbol_path"], "status": "gone", "at": date}
                    break
                if h != first_hash:
                    outcome = {
                        "symbol_path": a["symbol_path"],
                        "status": "changed",
                        "since": revs[0][1],
                        "at": date,
                    }
                    break
            else:
                outcome["at"] = revs[-1][1]
            decay.append(outcome)
    metrics["decay"] = {
        "n_tracked": len(decay),
        "by_status": dict(Counter(d["status"] for d in decay)),
        # File paths name repo files, not people — but strip them from the
        # committed-safe metrics anyway; full detail lives only in OUT_DIR.
        "details": [
            {"status": d["status"], "since": d.get("since"), "at": d.get("at")} for d in decay
        ],
    }

    with open(out_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, sort_keys=True)

    # Sample manifest (no text) + private statement file (never committed).
    rng = random.Random(args.seed)
    order = list(range(len(candidates)))
    rng.shuffle(order)
    sample_idx = order[: min(SAMPLE_TARGET, len(candidates))]
    manifest = [
        {
            "idx": i,
            "req_id": candidates[i]["req_id"],
            "session_id": candidates[i]["session_id"],
        }
        for i in sample_idx
    ]
    with open(out_dir / "sample_manifest.json", "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "sample": manifest}, f, indent=2)
    statements = [{"idx": i, "statement": candidates[i]["statement"]} for i in sample_idx]
    with open(out_dir / "sample_statements.json", "w", encoding="utf-8") as f:
        json.dump(statements, f, indent=2)

    print(f"sessions={len(by_session)} prompts={len(prompts)} candidates={len(candidates)}")
    print(f"wrote {out_dir}/metrics.json sample_manifest.json sample_statements.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
