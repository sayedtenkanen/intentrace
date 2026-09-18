"""Tests for deterministic symbol resolution (B1) and ambiguity reporting (R3)."""

from __future__ import annotations

from intentrace.anchor.python import build_anchor, parse_source
from intentrace.extract.fake import FakeExtractor
from intentrace.models import AnchorRef, Observation
from intentrace.symbol import Ambiguous, SymbolTable


def test_same_name_different_files_deterministic() -> None:
    """Two functions named 'attempt' in different files resolve by qualified path."""
    source = b"def attempt():\n    pass\n"

    tree_a = parse_source(source)
    anchor_a = build_anchor("src/retry.py", tree_a, source, "attempt")
    assert anchor_a is not None

    tree_b = parse_source(source)
    anchor_b = build_anchor("lib/retry.py", tree_b, source, "attempt")
    assert anchor_b is not None

    table = SymbolTable()
    table.by_qualified[anchor_a.symbol_path] = anchor_a
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_bare["attempt"] = [anchor_a, anchor_b]

    # Qualified lookup is deterministic
    assert table.by_qualified["src/retry.py::attempt"] is anchor_a
    assert table.by_qualified["lib/retry.py::attempt"] is anchor_b

    # Bare lookup is ambiguous — two candidates
    result = table.resolve("attempt")
    assert isinstance(result, Ambiguous)
    assert len(result.candidates) == 2


def test_ambiguous_bare_name_not_anchored() -> None:
    """An ambiguous bare name produces no anchor in extracted requirements."""
    source = b"def attempt():\n    pass\n"

    tree_a = parse_source(source)
    anchor_a = build_anchor("src/retry.py", tree_a, source, "attempt")

    tree_b = parse_source(source)
    anchor_b = build_anchor("lib/retry.py", tree_b, source, "attempt")

    table = SymbolTable()
    table.by_qualified[anchor_a.symbol_path] = anchor_a
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_bare["attempt"] = [anchor_a, anchor_b]

    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The attempt function must retry on failure.",
    )

    extractor = FakeExtractor()
    result = extractor.extract([obs], symbols=table)

    # The requirement exists but has no anchors — ambiguous name was skipped
    assert len(result.requirements) == 1
    assert len(result.requirements[0].anchors) == 0
    # And the ambiguity is reported
    assert "attempt" in result.ambiguous_symbols
    assert len(result.ambiguous_symbols["attempt"]) == 2


def test_ambiguous_name_reported_with_candidates() -> None:
    """R3: ambiguous symbol surfaces both candidates by qualified path."""
    source = b"def attempt():\n    pass\n"

    tree_a = parse_source(source)
    anchor_a = build_anchor("src/retry.py", tree_a, source, "attempt")

    tree_b = parse_source(source)
    anchor_b = build_anchor("lib/retry.py", tree_b, source, "attempt")

    table = SymbolTable()
    table.by_qualified[anchor_a.symbol_path] = anchor_a
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_bare["attempt"] = [anchor_a, anchor_b]

    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The attempt function must retry on failure.",
    )

    extractor = FakeExtractor()
    result = extractor.extract([obs], symbols=table)

    # Ambiguity is reported with both qualified paths
    assert "attempt" in result.ambiguous_symbols
    paths = result.ambiguous_symbols["attempt"]
    assert "src/retry.py::attempt" in paths
    assert "lib/retry.py::attempt" in paths


def test_unique_bare_name_resolves() -> None:
    """A unique bare name resolves to a single anchor."""
    source = b"def attempt():\n    pass\n"

    tree = parse_source(source)
    anchor = build_anchor("retry.py", tree, source, "attempt")
    assert anchor is not None

    table = SymbolTable()
    table.by_qualified[anchor.symbol_path] = anchor
    table.by_bare["attempt"] = [anchor]

    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The attempt function must retry on failure.",
    )

    extractor = FakeExtractor()
    result = extractor.extract([obs], symbols=table)

    assert len(result.requirements) == 1
    assert len(result.requirements[0].anchors) == 1
    assert result.requirements[0].anchors[0].symbol_path == "retry.py::attempt"


def test_ambiguous_sentence_drops_all_anchors() -> None:
    """One ambiguous symbol in a sentence leaves the requirement unanchored.

    Even the definitively resolved symbols in that sentence are dropped:
    a partially anchored requirement would present unsettled code targets
    as settled.
    """
    source = b"def attempt():\n    pass\n"

    tree_a = parse_source(source)
    anchor_a = build_anchor("src/retry.py", tree_a, source, "attempt")

    tree_b = parse_source(source)
    anchor_b = build_anchor("lib/retry.py", tree_b, source, "attempt")

    helper_source = b"def helper():\n    pass\n"
    helper_tree = parse_source(helper_source)
    helper_anchor = build_anchor("src/util.py", helper_tree, helper_source, "helper")
    assert helper_anchor is not None

    table = SymbolTable()
    table.by_qualified[anchor_a.symbol_path] = anchor_a
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_qualified[helper_anchor.symbol_path] = helper_anchor
    table.by_bare["attempt"] = [anchor_a, anchor_b]
    table.by_bare["helper"] = [helper_anchor]

    obs = Observation.create(
        session_id="s1",
        turn_index=0,
        kind="prompt",
        text="The attempt function must retry and the helper function should always log.",
    )

    extractor = FakeExtractor()
    result = extractor.extract([obs], symbols=table)

    assert len(result.requirements) == 1
    assert result.requirements[0].anchors == []
    assert "attempt" in result.ambiguous_symbols
    assert len(result.ambiguous_symbols["attempt"]) == 2


def _class_anchor(file_path: str, class_name: str, method_name: str = "attempt") -> AnchorRef:
    """Build an anchor for a method nested in a class, parsed standalone."""
    source = f"class {class_name}:\n    def {method_name}(self):\n        pass\n".encode()
    tree = parse_source(source)
    anchor = build_anchor(file_path, tree, source, method_name)
    assert anchor is not None
    assert isinstance(anchor, AnchorRef)
    return anchor


def test_class_method_resolves_named_class() -> None:
    """A Class.method reference resolves to the named class, not bare lookup.

    Previously the class name was discarded, so same-named methods in two
    classes reported ambiguity instead of resolving to the named one.

    White-box: _split_sentences tears "X.y" across two segments (every
    period splits), so Class.method never reaches resolution through
    extract() — see D4. This pins _resolve_symbols directly.
    """
    from intentrace.extract.fake import _resolve_symbols

    anchor_a = _class_anchor("x.py", "Alpha")
    anchor_b = _class_anchor("y.py", "Beta")
    assert anchor_a.symbol_path == "x.py::Alpha::attempt"
    assert anchor_b.symbol_path == "y.py::Beta::attempt"

    table = SymbolTable()
    table.by_qualified[anchor_a.symbol_path] = anchor_a
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_bare["attempt"] = [anchor_a, anchor_b]

    anchors, ambiguous = _resolve_symbols(
        "The Alpha.attempt function must retry on failure.", table
    )

    assert [a.symbol_path for a in anchors] == ["x.py::Alpha::attempt"]
    assert ambiguous == []


def test_class_method_ambiguous_across_files() -> None:
    """Same Class.method in two files stays ambiguous, with exact candidates.

    The report names the qualified matches only — a bare same-named
    symbol elsewhere must not leak into it. (White-box: see D4.)
    """
    from intentrace.extract.fake import _resolve_symbols

    anchor_x = _class_anchor("x.py", "Foo")
    anchor_y = _class_anchor("y.py", "Foo")

    decoy_source = b"def attempt():\n    pass\n"
    decoy_tree = parse_source(decoy_source)
    decoy = build_anchor("z.py", decoy_tree, decoy_source, "attempt")
    assert decoy is not None

    table = SymbolTable()
    table.by_qualified[anchor_x.symbol_path] = anchor_x
    table.by_qualified[anchor_y.symbol_path] = anchor_y
    table.by_qualified[decoy.symbol_path] = decoy
    table.by_bare["attempt"] = [anchor_x, anchor_y, decoy]

    anchors, ambiguous = _resolve_symbols("The Foo.attempt function must retry on failure.", table)

    assert anchors == []
    assert len(ambiguous) == 1
    name, candidates = ambiguous[0]
    assert name == "attempt"
    assert [c.symbol_path for c in candidates] == [
        "x.py::Foo::attempt",
        "y.py::Foo::attempt",
    ]


def test_class_method_falls_back_to_bare_name() -> None:
    """A Class.method naming no known class falls back to the bare method."""
    from intentrace.extract.fake import _resolve_symbols

    anchor_b = _class_anchor("y.py", "Beta")

    table = SymbolTable()
    table.by_qualified[anchor_b.symbol_path] = anchor_b
    table.by_bare["attempt"] = [anchor_b]

    anchors, ambiguous = _resolve_symbols(
        "The Nope.attempt function must retry on failure.", table
    )

    assert [a.symbol_path for a in anchors] == ["y.py::Beta::attempt"]
    assert ambiguous == []
