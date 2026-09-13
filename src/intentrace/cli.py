"""CLI entry point for intentrace.

Commands:
  intentrace ingest <observations.jsonl>  — dev-only; appends observations to the log
  intentrace why <path>[:<line>]          — show intent covering that code
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from intentrace.anchor.python import (
    _symbol_path,
    build_anchor,
    parse_source,
    resolve_line_to_node,
)
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


def _build_symbol_table(repo_root: Path) -> dict[str, AnchorRef]:
    """Scan all Python files in the repo and build a symbol name -> AnchorRef mapping."""
    symbols: dict[str, AnchorRef] = {}
    for py_file in repo_root.rglob("*.py"):
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_bytes()
        except OSError:
            continue
        tree = parse_source(source)
        repo_rel = str(py_file.relative_to(repo_root))
        _collect_symbols(tree, repo_rel, source, symbols)
    return symbols


def _collect_symbols(
    tree: object,
    file_path: str,
    source: bytes,
    symbols: dict[str, AnchorRef],
) -> None:
    """Collect function and class symbols from a tree-sitter tree.

    build_anchor already traverses the tree to find symbols, so we just
    need to find the names of all function/class definitions and let
    build_anchor do the lookup.
    """
    import tree_sitter

    if not isinstance(tree, tree_sitter.Tree):
        return

    root = tree.root_node
    _collect_symbols_from_node(root, file_path, tree, source, symbols)


def _collect_symbols_from_node(
    node: object,
    file_path: str,
    tree: object,
    source: bytes,
    symbols: dict[str, AnchorRef],
) -> None:
    """Recursively collect symbols from a tree-sitter node."""
    import tree_sitter

    if not isinstance(node, tree_sitter.Node):
        return

    if node.type in ("function_definition", "class_definition"):
        name = _node_name(node, source)
        if name and name not in symbols:
            anchor = build_anchor(file_path, tree, source, name)  # type: ignore[arg-type]
            if anchor:
                symbols[name] = anchor

    for child in node.children:
        _collect_symbols_from_node(child, file_path, tree, source, symbols)


def _node_name(node: object, source: bytes) -> str:
    """Get the name of a function or class node."""
    import tree_sitter

    if not isinstance(node, tree_sitter.Node):
        return ""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return ""
    return source[name_node.start_byte : name_node.end_byte].decode()


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
            try:
                obs = Observation.model_validate(data)
            except ValidationError as e:
                print(
                    f"error: invalid observation on line {line_num}: {e}",
                    file=sys.stderr,
                )
                return 1
            append_observation(repo_root, obs)

    return 0


def _format_requirement(req: Requirement, obs: Observation | None) -> str:
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

    # Show quoted span from the observation text (the human's words)
    if obs and req.provenance:
        span = req.provenance[0]
        quoted = obs.text[span.start : span.end]
        if len(quoted) > 60:
            quoted = quoted[:57] + "..."
        lines.append(f'  quoted     "{quoted}"')
        ts = obs.timestamp.strftime("%Y-%m-%d")
        session_turn = f"session {obs.session_id[:4]} \u00b7 turn {obs.turn_index}"
        lines.append(f"  said       {ts}  {session_turn}")

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
    if result.corrupt_line:
        print(f"error: {result.corrupt_line}", file=sys.stderr)
        return 1

    if not result.observations:
        print(
            "no observations recorded — run 'intentrace ingest' first",
            file=sys.stderr,
        )
        return 1

    # Build symbol table and extract requirements with anchors
    symbols = _build_symbol_table(repo_root)
    extractor = FakeExtractor()
    requirements = extractor.extract(result.observations, symbols=symbols)

    # Build a store to hold them
    store = MemoryStore(repo_root)
    store.add_requirements(requirements)

    # Find which symbol encloses the target line
    target_symbol: str | None = None
    if line_num is not None:
        node = resolve_line_to_node(tree, line_num, source)
        if node is not None and node.type != "module":
            target_symbol = _symbol_path(repo_rel, node, source)

    # Find matching requirements by reading stored anchors only
    found: list[tuple[Requirement, Observation | None]] = []
    seen_reqs: set[str] = set()

    for req in requirements:
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

    if not found:
        print("no intent covers this code")
        return 0

    for req, obs in found:
        print(_format_requirement(req, obs))
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
