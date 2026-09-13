"""CLI entry point for intentrace.

Commands:
  intentrace ingest <observations.jsonl>  — dev-only; appends observations to the log
  intentrace why <path>[:<line>]          — show intent covering that code
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tree_sitter

from intentrace.anchor.python import build_anchor, parse_source, resolve_line_to_node
from intentrace.extract.fake import FakeExtractor
from intentrace.log import append_observation, read_observations
from intentrace.models import AnchorRef, Observation, Requirement
from intentrace.store import MemoryStore


def _find_repo_root() -> Path:
    """Walk up from cwd to find a .intentrace directory or git repo."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".intentrace").exists() or (parent / ".git").exists():
            return parent
    return current


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest observations from a JSONL file into the log."""
    repo_root = _find_repo_root()
    input_path = Path(args.observations_file)

    if not input_path.exists():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    with open(input_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"error: invalid JSON on line {line_num}", file=sys.stderr)
                return 1
            obs = Observation.model_validate(data)
            append_observation(repo_root, obs)

    return 0


def _format_requirement(req: Requirement, obs: Observation | None, source_text: str | None) -> str:
    """Format a requirement for display."""
    lines: list[str] = []
    req_short = f"R-{req.req_id[:8]}"
    maturity = req.maturity
    # In Slice 1, all requirements are sketches — never satisfied (I2/I3)
    status = "unverified"
    lines.append(f"{req_short}   {maturity} \u00b7 {status}")
    lines.append(f'  "{req.statement}"')
    lines.append("")
    lines.append(f"  origin     {req.origin}")

    if obs:
        ts = obs.timestamp.strftime("%Y-%m-%d")
        session_turn = f"session {obs.session_id[:4]} \u00b7 turn {obs.turn_index}"
        lines.append(f"  said       {ts}  {session_turn}")

    # Show quoted span from source text
    if source_text and req.provenance:
        span = req.provenance[0]
        quoted = source_text[span.start : span.end]
        if len(quoted) > 60:
            quoted = quoted[:57] + "..."
        lines.append(f'  quoted     "{quoted}"')

    lines.append("  ratified   \u2014")  # never ratified in Slice 1

    if req.anchors:
        anchor_strs = [a.symbol_path for a in req.anchors]
        lines.append(f"  anchors    {', '.join(anchor_strs)}")
    else:
        lines.append("  anchors    none")

    if req.evidence:
        evidence_strs = [f"{e.kind}:{e.ref}" for e in req.evidence]
        lines.append(f"  evidence   {', '.join(evidence_strs)}")
    else:
        lines.append("  evidence   none \u2014 nothing is checking this")

    return "\n".join(lines)


def _resolve_symbols_from_statement(
    statement: str, file_path: str, tree: tree_sitter.Tree, source: bytes
) -> list[AnchorRef]:
    """Try to resolve symbols mentioned in a requirement statement to code anchors."""
    # Look for Class.method patterns
    class_method_re = re.compile(r"\b([A-Z][A-Za-z0-9_]+)\.([a-z][A-Za-z0-9_]+)\b")
    # Look for standalone function/method names
    word_re = re.compile(r"\b([a-z][A-Za-z0-9_]+)\b")

    anchors: list[AnchorRef] = []
    seen: set[str] = set()

    # Try Class.method first
    for match in class_method_re.finditer(statement):
        _class_name, method_name = match.groups()
        # Try the method name as a symbol
        anchor = build_anchor(file_path, tree, source, method_name)
        if anchor and anchor.symbol_path not in seen:
            anchors.append(anchor)
            seen.add(anchor.symbol_path)

    # Try standalone words (skip common English words)
    skip_words = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "must",
        "should",
        "never",
        "always",
        "don't",
        "do",
        "not",
        "it",
        "a",
        "an",
        "in",
        "on",
        "to",
        "of",
        "is",
        "be",
        "as",
        "at",
    }
    for match in word_re.finditer(statement):
        word = match.group(1)
        if word not in skip_words and word not in seen:
            anchor = build_anchor(file_path, tree, source, word)
            if anchor and anchor.symbol_path not in seen:
                anchors.append(anchor)
                seen.add(anchor.symbol_path)

    return anchors


def cmd_why(args: argparse.Namespace) -> int:
    """Show intent covering a code location."""
    repo_root = _find_repo_root()
    target = args.target

    # Parse path:line
    if ":" in target:
        path_str, line_str = target.rsplit(":", 1)
        try:
            line_num = int(line_str)
        except ValueError:
            print(f"error: invalid line number: {line_str}", file=sys.stderr)
            return 1
    else:
        path_str = target
        line_num = None

    file_path = Path(path_str)
    if not file_path.exists():
        # Try relative to repo root
        file_path = repo_root / path_str
    if not file_path.exists():
        print(f"error: file not found: {path_str}", file=sys.stderr)
        return 1

    source = file_path.read_bytes()
    if file_path.is_relative_to(repo_root):
        repo_rel = str(file_path.relative_to(repo_root))
    else:
        repo_rel = path_str

    # Parse the source
    tree = parse_source(source)

    # Read the log
    result = read_observations(repo_root)
    if result.torn_line:
        print(f"warning: {result.torn_line}", file=sys.stderr)

    # Extract requirements
    extractor = FakeExtractor()
    requirements = extractor.extract(result.observations)

    # Build a store to hold them
    store = MemoryStore(repo_root)
    store.add_requirements(requirements)

    # Find which symbol encloses the target line
    target_symbol: str | None = None
    if line_num is not None:
        node = resolve_line_to_node(tree, line_num, source)
        if node is not None and node.type != "module":
            from intentrace.anchor.python import _symbol_path

            target_symbol = _symbol_path(repo_rel, node, source)

    # Find matching requirements by resolving symbols from statements
    found: list[tuple[Requirement, Observation | None]] = []
    seen_reqs: set[str] = set()

    for req in requirements:
        # First check if requirement already has anchors for this file
        for anchor in req.anchors:
            if (
                anchor.file == repo_rel
                and (target_symbol is None or anchor.symbol_path == target_symbol)
                and req.req_id not in seen_reqs
            ):
                obs = None
                if req.provenance:
                    obs = store.get_observation(req.provenance[0].obs_id)
                found.append((req, obs))
                seen_reqs.add(req.req_id)

        # Also try to resolve symbols from the statement
        resolved_anchors = _resolve_symbols_from_statement(req.statement, repo_rel, tree, source)
        for anchor in resolved_anchors:
            if (
                target_symbol is None or anchor.symbol_path == target_symbol
            ) and req.req_id not in seen_reqs:
                obs = None
                if req.provenance:
                    obs = store.get_observation(req.provenance[0].obs_id)
                found.append((req, obs))
                seen_reqs.add(req.req_id)

    if not found:
        print("no intent covers this code")
        return 0

    for req, obs in found:
        source_text = source.decode() if source else None
        print(_format_requirement(req, obs, source_text))
        print()

    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="intentrace")
    subparsers = parser.add_subparsers(dest="command")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="Ingest observations from a JSONL file")
    ingest_parser.add_argument("observations_file", help="Path to observations JSONL file")

    # why
    why_parser = subparsers.add_parser("why", help="Show intent covering a code location")
    why_parser.add_argument("target", help="path:line to inspect")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "ingest":
        return cmd_ingest(args)
    elif args.command == "why":
        return cmd_why(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
