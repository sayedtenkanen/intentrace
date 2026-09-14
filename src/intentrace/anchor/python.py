"""Python anchorer using tree-sitter.

Parses Python source, extracts symbol paths, computes normalized node hashes,
and resolves path:line to the innermost enclosing function or class.
"""

from __future__ import annotations

import hashlib

import tree_sitter
import tree_sitter_python as tspython

from intentrace.models import AnchorRef

_lang = tree_sitter.Language(tspython.language())
_parser = tree_sitter.Parser(_lang)


def parse_source(source: bytes) -> tree_sitter.Tree:
    """Parse Python source into a tree-sitter tree."""
    return _parser.parse(source)


def symbol_path(file_path: str, node: tree_sitter.Node, source: bytes) -> str:
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
    return file_path + "::" + "::".join(parts) if parts else file_path


def _normalize_subtree(node: tree_sitter.Node, source: bytes) -> str:
    """Normalize a subtree for hashing: exclude comments, docstrings, and whitespace.

    Excludes:
    - comment nodes
    - leading docstrings (expression_statement containing a string as first child
      of a function/class/module body)

    Preserves all other string literals (they are behaviour).
    """
    if node.type == "comment":
        return ""

    # Exclude leading docstrings: an expression_statement whose only significant
    # child is a string, at the start of a function/class/module body
    if node.type == "expression_statement" and _is_leading_docstring(node, source):
        return ""

    parts: list[str] = []

    if node.child_count == 0:
        # Leaf node — include token text
        return source[node.start_byte : node.end_byte].decode()

    for child in node.children:
        normalized = _normalize_subtree(child, source)
        if normalized:
            parts.append(normalized)

    return " ".join(parts)


def _is_leading_docstring(node: tree_sitter.Node, source: bytes) -> bool:
    """Check if an expression_statement is a leading docstring.

    A leading docstring is an expression_statement that contains a single
    string literal, positioned at the start of a function, class, or module body.
    """
    if node.type != "expression_statement":
        return False

    # Must have exactly one child that is a string
    if node.child_count != 1:
        return False

    child = node.child(0)
    if child is None or child.type != "string":
        return False

    # Check that the parent is a function/class body or module
    parent = node.parent
    if parent is None:
        return False

    if parent.type == "module":
        return True

    if parent.type == "block":
        # Check if this is the first statement in the block
        grandparent = parent.parent
        if grandparent is not None and grandparent.type in (
            "function_definition",
            "class_definition",
        ):
            # Check if this is the first child of the block
            for i in range(parent.child_count):
                child_node = parent.child(i)
                if child_node is not None and child_node.type != "comment":
                    return child_node == node

    return False


def node_hash(node: tree_sitter.Node, source: bytes) -> str:
    """Compute a hash of the normalized subtree."""
    normalized = _normalize_subtree(node, source)
    return hashlib.sha256(normalized.encode()).hexdigest()


def resolve_line_to_node(
    tree: tree_sitter.Tree, line: int, source: bytes
) -> tree_sitter.Node | None:
    """Resolve a 1-indexed line number to the innermost enclosing function or class.

    Returns the most deeply nested function_definition or class_definition
    that contains the target line. Falls back to the module root if none.
    """
    root = tree.root_node
    target_row = line - 1  # tree-sitter uses 0-indexed rows

    best: tree_sitter.Node | None = None

    def _walk(node: tree_sitter.Node) -> None:
        nonlocal best
        for child in node.children:
            if (
                child.type in ("function_definition", "class_definition")
                and child.start_point[0] <= target_row <= child.end_point[0]
            ):
                best = child
            _walk(child)

    _walk(root)
    return best if best is not None else root


def build_anchor(
    file_path: str,
    tree: tree_sitter.Tree,
    source: bytes,
    symbol_name: str | None = None,
    node: tree_sitter.Node | None = None,
) -> AnchorRef | None:
    """Build an AnchorRef for a symbol.

    If node is provided, use it directly (deterministic — no search).
    If symbol_name is provided, search the tree for a matching node.
    """
    if node is not None:
        target: tree_sitter.Node | None = node
    elif symbol_name is not None:
        target = _find_symbol(tree.root_node, source, symbol_name)
    else:
        return None

    if target is None:
        return None

    return AnchorRef(
        lang="python",
        file=file_path,
        symbol_path=symbol_path(file_path, target, source),
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
