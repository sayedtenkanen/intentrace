"""Tests for Python anchoring with tree-sitter."""

from __future__ import annotations

from intentrace.anchor.python import (
    build_anchor,
    node_hash,
    parse_source,
    resolve_line_to_node,
    symbol_path,
)

SAMPLE_SOURCE = b"""\
class RetryPolicy:
    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def attempt(self, func, *args):
        last_error = None
        for i in range(self.max_retries):
            try:
                return func(*args)
            except Exception as e:
                last_error = e
        raise last_error


def format_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"
"""


def test_symbol_path_nested_class_method() -> None:
    """Symbol path for a nested class -> def."""
    tree = parse_source(SAMPLE_SOURCE)
    # Find the 'attempt' method (inside RetryPolicy)
    # Navigate to the method
    for child in tree.root_node.children:
        if child.type == "class_definition":
            for grandchild in child.children:
                if grandchild.type == "block":
                    for node in grandchild.children:
                        if node.type == "function_definition":
                            name = node.child_by_field_name("name")
                            if (
                                name
                                and SAMPLE_SOURCE[name.start_byte : name.end_byte] == b"attempt"
                            ):
                                path = symbol_path("retry.py", node, SAMPLE_SOURCE)
                                assert "RetryPolicy" in path
                                assert "attempt" in path
                                assert "retry.py::" in path
                                return
    raise AssertionError("attempt method not found")


def test_node_hash_ignores_comments() -> None:
    """Adding a comment does not change node_hash."""
    source_a = b"def foo():\n    return 42\n"
    source_b = b"def foo():\n    # this is a comment\n    return 42\n"

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    assert node_hash(func_a, source_a) == node_hash(func_b, source_b)


def test_node_hash_ignores_reformatting() -> None:
    """Reformatting does not change node_hash."""
    source_a = b"def foo():\n    return 42\n"
    source_b = b"def foo():\n    return   42\n"

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    # Both are function_definition nodes - hashes should match
    # (normalization preserves structure and literals)
    assert node_hash(func_a, source_a) == node_hash(func_b, source_b)


def test_node_hash_changes_on_literal() -> None:
    """Changing a literal does change node_hash."""
    source_a = b"def foo():\n    return 42\n"
    source_b = b"def foo():\n    return 99\n"

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    assert node_hash(func_a, source_a) != node_hash(func_b, source_b)


def test_node_hash_unaffected_by_sibling() -> None:
    """Renaming an unrelated sibling function does not change this node's hash."""
    source_a = b"def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    source_b = b"def foo():\n    return 1\n\ndef baz():\n    return 2\n"

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    # Get the first function (foo) from both
    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    assert func_a.type == "function_definition"
    assert func_b.type == "function_definition"
    assert node_hash(func_a, source_a) == node_hash(func_b, source_b)


def test_node_hash_ignores_docstring() -> None:
    """Editing a docstring does not change node_hash."""
    source_a = b'def foo():\n    """Original docstring."""\n    return 42\n'
    source_b = b'def foo():\n    """Modified docstring."""\n    return 42\n'

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    assert node_hash(func_a, source_a) == node_hash(func_b, source_b)


def test_node_hash_changes_on_return_string() -> None:
    """Changing a string used in a return value does change node_hash."""
    source_a = b'def foo():\n    return "hello"\n'
    source_b = b'def foo():\n    return "world"\n'

    tree_a = parse_source(source_a)
    tree_b = parse_source(source_b)

    func_a = tree_a.root_node.child(0)
    func_b = tree_b.root_node.child(0)

    assert node_hash(func_a, source_a) != node_hash(func_b, source_b)


def test_resolve_line_to_method() -> None:
    """path:line inside a method resolves to that method, not the class."""
    tree = parse_source(SAMPLE_SOURCE)
    # Line 7 is inside attempt() method (0-indexed: line 6)
    node = resolve_line_to_node(tree, 7, SAMPLE_SOURCE)
    assert node is not None
    assert node.type == "function_definition"
    name = node.child_by_field_name("name")
    assert name is not None
    assert SAMPLE_SOURCE[name.start_byte : name.end_byte] == b"attempt"


def test_resolve_line_to_class() -> None:
    """Line at class level resolves to the class."""
    tree = parse_source(SAMPLE_SOURCE)
    # Line 1 is the class definition
    node = resolve_line_to_node(tree, 1, SAMPLE_SOURCE)
    assert node is not None
    assert node.type == "class_definition"


def test_resolve_line_module_level() -> None:
    """Line at module level (outside any function/class) resolves to module."""
    tree = parse_source(SAMPLE_SOURCE)
    # Line 16 is the format_error function, but let's test module-level
    # Add a line at the top
    source = b"X = 1\n" + SAMPLE_SOURCE
    tree = parse_source(source)
    node = resolve_line_to_node(tree, 1, source)
    assert node is not None
    assert node.type == "module"


def test_build_anchor_found() -> None:
    """build_anchor returns an AnchorRef for a known symbol."""
    tree = parse_source(SAMPLE_SOURCE)
    anchor = build_anchor("retry.py", tree, SAMPLE_SOURCE, "attempt")
    assert anchor is not None
    assert anchor.lang == "python"
    assert anchor.file == "retry.py"
    assert "attempt" in anchor.symbol_path
    assert "RetryPolicy" in anchor.symbol_path


def test_build_anchor_not_found() -> None:
    """build_anchor returns None for unknown symbol."""
    tree = parse_source(SAMPLE_SOURCE)
    anchor = build_anchor("retry.py", tree, SAMPLE_SOURCE, "nonexistent")
    assert anchor is None


def test_symbol_path_includes_directory() -> None:
    """Two files with the same basename in different directories produce different symbol paths."""
    source = b"def foo():\n    pass\n"
    tree = parse_source(source)

    anchor_a = build_anchor("src/pkg/retry.py", tree, source, "foo")
    anchor_b = build_anchor("tests/fixtures/retry.py", tree, source, "foo")

    assert anchor_a is not None
    assert anchor_b is not None
    assert anchor_a.symbol_path != anchor_b.symbol_path
    assert anchor_a.symbol_path == "src/pkg/retry.py::foo"
    assert anchor_b.symbol_path == "tests/fixtures/retry.py::foo"
