"""Symbol table for anchor resolution.

Keyed by qualified path for determinism. Bare-name lookup from a sentence
returns all candidates; ambiguity is reported, not resolved by guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import tree_sitter

from intentrace.anchor.python import build_anchor, parse_source
from intentrace.models import AnchorRef


@dataclass(frozen=True)
class Resolved:
    """Exactly one candidate found — definitive resolution."""

    kind: Literal["resolved"] = field(default="resolved", init=False)
    anchor: AnchorRef = field(default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class Ambiguous:
    """Multiple candidates found — the caller must not guess.

    The `candidates` list carries all options so that the ambiguity
    can be reported with their qualified paths.
    """

    kind: Literal["ambiguous"] = field(default="ambiguous", init=False)
    candidates: tuple[AnchorRef, ...] = ()


@dataclass(frozen=True)
class NotFound:
    """No candidate found."""

    kind: Literal["not_found"] = field(default="not_found", init=False)


Resolution = Resolved | Ambiguous | NotFound


@dataclass
class SymbolTable:
    """Index of symbols found in the repository.

    Primary index: qualified path -> AnchorRef (deterministic, unique).
    Secondary index: bare name -> list[AnchorRef] (for sentence resolution).
    """

    by_qualified: dict[str, AnchorRef] = field(default_factory=dict)
    by_bare: dict[str, list[AnchorRef]] = field(default_factory=dict)

    def resolve(self, name: str) -> Resolution:
        """Resolve a bare symbol name.

        Returns Resolved if exactly one candidate exists.
        Returns Ambiguous if multiple candidates exist (with all candidates).
        Returns NotFound if no candidate exists.
        """
        candidates = self.by_bare.get(name, [])
        if len(candidates) == 0:
            return NotFound()
        if len(candidates) == 1:
            return Resolved(anchor=candidates[0])
        return Ambiguous(candidates=tuple(candidates))

    def resolve_qualified(self, class_name: str, method_name: str) -> Resolution:
        """Resolve a Class.method reference from a sentence.

        Matches qualified paths ending in ::Class::method, so naming the
        class resolves deterministically even when the bare method name is
        ambiguous. The leading :: in the match keeps partial class names
        from matching (AA never matches A). Candidates are sorted by path
        so ambiguity reports are deterministic.
        """
        suffix = f"::{class_name}::{method_name}"
        candidates = sorted(
            (a for a in self.by_qualified.values() if a.symbol_path.endswith(suffix)),
            key=lambda a: a.symbol_path,
        )
        if len(candidates) == 0:
            return NotFound()
        if len(candidates) == 1:
            return Resolved(anchor=candidates[0])
        return Ambiguous(candidates=tuple(candidates))


def build_symbol_table(repo_root: Path) -> SymbolTable:
    """Scan all Python files and build a symbol table keyed by qualified path."""
    table = SymbolTable()
    for py_file in repo_root.rglob("*.py"):
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_bytes()
        except OSError:
            continue
        tree = parse_source(source)
        if not isinstance(tree, tree_sitter.Tree):
            continue
        repo_rel = str(py_file.relative_to(repo_root))
        _collect_symbols(tree, repo_rel, source, table)
    return table


def _collect_symbols(
    tree: tree_sitter.Tree,
    file_path: str,
    source: bytes,
    table: SymbolTable,
) -> None:
    """Collect function and class symbols from a tree-sitter tree.

    Recurses into nested classes and functions so that methods like
    RetryPolicy.attempt are indexed.
    """
    _collect_symbols_from_node(tree.root_node, file_path, tree, source, table)


def _collect_symbols_from_node(
    node: tree_sitter.Node,
    file_path: str,
    tree: tree_sitter.Tree,
    source: bytes,
    table: SymbolTable,
) -> None:
    """Recursively collect symbols from a tree-sitter node."""
    if node.type in ("function_definition", "class_definition"):
        anchor = build_anchor(file_path, tree, source, node=node)
        if anchor is not None:
            table.by_qualified[anchor.symbol_path] = anchor
            _index_bare_name(anchor, table)

    for child in node.children:
        _collect_symbols_from_node(child, file_path, tree, source, table)


def _index_bare_name(anchor: AnchorRef, table: SymbolTable) -> None:
    """Index an anchor by its bare symbol name (last component of symbol_path)."""
    bare = anchor.symbol_path.rsplit("::", 1)[-1]
    table.by_bare.setdefault(bare, []).append(anchor)
