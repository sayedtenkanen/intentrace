"""Tests for deterministic symbol resolution (B1) and ambiguity reporting (R3)."""

from __future__ import annotations

from intentrace.anchor.python import build_anchor, parse_source
from intentrace.extract.fake import FakeExtractor
from intentrace.models import Observation
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
