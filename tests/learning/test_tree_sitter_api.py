"""Learning test: verify tree-sitter Python API behavior.

This file documents the dependency's actual behavior, not our code.
Marked so CI can exclude it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.learning


def test_tree_sitter_version() -> None:
    import tree_sitter
    import tree_sitter_python as tspython

    lang = tree_sitter.Language(tspython.language())
    parser = tree_sitter.Parser(lang)
    tree = parser.parse(b"def foo():\n    pass\n")
    assert tree.root_node.type == "module"


def test_tree_sitter_node_types() -> None:
    import tree_sitter
    import tree_sitter_python as tspython

    lang = tree_sitter.Language(tspython.language())
    parser = tree_sitter.Parser(lang)
    tree = parser.parse(b"class Foo:\n    def bar(self):\n        pass\n")
    root = tree.root_node
    assert root.type == "module"
    assert root.child(0).type == "class_definition"


def test_tree_sitter_node_children() -> None:
    import tree_sitter
    import tree_sitter_python as tspython

    lang = tree_sitter.Language(tspython.language())
    parser = tree_sitter.Parser(lang)
    tree = parser.parse(b"def foo():\n    return 42\n")
    func = tree.root_node.child(0)
    assert func.type == "function_definition"
    assert func.child_by_field_name("name").text == b"foo"


def test_tree_sitter_byte_offsets() -> None:
    import tree_sitter
    import tree_sitter_python as tspython

    lang = tree_sitter.Language(tspython.language())
    parser = tree_sitter.Parser(lang)
    src = b"def foo():\n    pass\n"
    tree = parser.parse(src)
    func = tree.root_node.child(0)
    assert func.start_byte == 0
    # end_byte excludes trailing newline not part of the node
    assert func.end_byte <= len(src)
    assert src[func.start_byte : func.end_byte] == b"def foo():\n    pass"
