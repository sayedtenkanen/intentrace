"""Python anchorer using tree-sitter.

Parses Python source, extracts symbol paths, computes normalized node hashes,
and resolves path:line to the innermost enclosing function or class.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import tree_sitter
import tree_sitter_python as tspython

from intentrace.models import AnchorRef

_lang = tree_sitter.Language(tspython.language())
_parser = tree_sitter.Parser(_lang)


def parse_source(source: bytes) -> tree_sitter.Tree:
    """Parse Python source into a tree-sitter tree."""
    return _parser.parse(source)


def _symbol_path(file_path: str, node: tree_sitter.Node, source: bytes) -> str:
    """Build a symbol path like 'retry.py::RetryPolicy::attempt'."""
    parts: list[str] = []
    current: tree_sitter.Node | None = node

    while current is not None:
        if current.type == "function_definition" or current.type == "class_definition":
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                parts.append(source[name_node.start_byte : name_node.end_byte].decode())
        current = current.parent

    parts.reverse()
    return Path(file_path).name + "::" + "::".join(parts) if parts else Path(file_path).name


def _normalize_subtree(node: tree_sitter.Node, source: bytes) -> str:
    """Normalize a subtree for hashing: exclude comments and whitespace, keep structure."""
    if node.type == "comment":
        return ""

    parts: list[str] = []

    if node.child_count == 0:
        # Leaf node — include token text
        text = source[node.start_byte : node.end_byte].decode()
        if node.type == "string":
            return text  # keep string literals as-is
        return text

    for child in node.children:
        normalized = _normalize_subtree(child, source)
        if normalized:
            parts.append(normalized)

    return " ".join(parts)


def node_hash(node: tree_sitter.Node, source: bytes) -> str:
    """Compute a hash of the normalized subtree."""
    normalized = _normalize_subtree(node, source)
    return hashlib.sha256(normalized.encode()).hexdigest()


def resolve_line_to_node(
    tree: tree_sitter.Tree, line: int, source: bytes
) -> tree_sitter.Node | None:
    """Resolve a 1-indexed line number to the innermost enclosing function or class.

    Falls back to the module (root) if no function/class encloses the line.
    """
    root = tree.root_node
    target_row = line - 1  # tree-sitter uses 0-indexed rows

    best: tree_sitter.Node | None = None

    def _walk(node: tree_sitter.Node) -> None:
        nonlocal best
        if (
            node.type in ("function_definition", "class_definition")
            and node.start_point[0] <= target_row <= node.end_point[0]
        ):
            best = node
        for child in node.children:
            _walk(child)

    _walk(root)
    return best if best is not None else root


def build_anchor(
    file_path: str,
    tree: tree_sitter.Tree,
    source: bytes,
    symbol_name: str,
) -> AnchorRef | None:
    """Find a symbol in the tree and build an AnchorRef for it."""
    root = tree.root_node
    target = _find_symbol(root, source, symbol_name)
    if target is None:
        return None

    return AnchorRef(
        lang="python",
        file=file_path,
        symbol_path=_symbol_path(file_path, target, source),
        node_kind=target.type,
        node_hash=node_hash(target, source),
    )


def _find_symbol(
    node: tree_sitter.Node, source: bytes, symbol_name: str
) -> tree_sitter.Node | None:
    """Find a node matching the symbol name."""
    if node.type == "function_definition" or node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            name = source[name_node.start_byte : name_node.end_byte].decode()
            if name == symbol_name:
                return node

    for child in node.children:
        result = _find_symbol(child, source, symbol_name)
        if result is not None:
            return result

    return None
