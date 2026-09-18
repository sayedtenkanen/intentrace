"""FakeExtractor — deterministic, rule-based requirement extraction.

No model in the loop. Considers only prompt observations, splits into
sentences, and emits requirements for sentences containing normative markers.
Symbols are resolved against a provided symbol table at extraction time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from intentrace.extract.port import ExtractionResult
from intentrace.models import AnchorRef, Observation, Requirement, Span
from intentrace.symbol import Ambiguous, Resolved, SymbolTable

VERSION = "fake-v1"


# Normative markers (case-insensitive)
NORMATIVE_RE = re.compile(
    r"\b(must|should|never|always|don't|do not)\b",
    re.IGNORECASE,
)

# Token patterns for symbol matching: word, word.word, or Class.method
CLASS_METHOD_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]+)\.([a-z][A-Za-z0-9_]+)\b")
WORD_RE = re.compile(r"\b([a-z][A-Za-z0-9_]+)\b")

# Common English words to skip when looking for symbols
SKIP_WORDS = frozenset(
    {
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
)


def _is_normative(sentence: str) -> bool:
    """Check if a sentence contains a normative marker."""
    return bool(NORMATIVE_RE.search(sentence))


def _resolve_symbols(sentence: str, symbols: SymbolTable) -> tuple[list[AnchorRef], list[str]]:
    """Resolve symbols mentioned in a sentence against the symbol table.

    Returns a tuple of (anchors, ambiguous_names). Only definitive
    (unambiguous) resolutions are returned as anchors. Ambiguous
    names are collected for reporting — guessing is an I4-class failure.
    """
    anchors: list[AnchorRef] = []
    ambiguous_names: list[str] = []
    seen: set[str] = set()

    # Try Class.method first — look up by bare method name
    for match in CLASS_METHOD_RE.finditer(sentence):
        _class_name, method_name = match.groups()
        resolution = symbols.resolve(method_name)
        if isinstance(resolution, Resolved) and method_name not in seen:
            anchors.append(resolution.anchor)
            seen.add(method_name)
        elif isinstance(resolution, Ambiguous) and method_name not in seen:
            ambiguous_names.append(method_name)
            seen.add(method_name)

    # Try standalone words — only definitive resolutions
    for match in WORD_RE.finditer(sentence):
        word = match.group(1)
        if word in SKIP_WORDS or word in seen:
            continue
        resolution = symbols.resolve(word)
        if isinstance(resolution, Resolved):
            anchors.append(resolution.anchor)
            seen.add(word)
        elif isinstance(resolution, Ambiguous):
            ambiguous_names.append(word)
            seen.add(word)

    return anchors, ambiguous_names


def _normalize_statement(text: str) -> str:
    """Normalize whitespace in a statement."""
    return " ".join(text.split())


class FakeExtractor:
    """Deterministic, rule-based extractor for Slice 1."""

    version: str = VERSION

    def extract(
        self,
        observations: Iterable[Observation],
        symbols: SymbolTable | None = None,
    ) -> ExtractionResult:
        """Extract requirements from prompt observations.

        Args:
            observations: The observations to extract from.
            symbols: Optional symbol table for anchor resolution.
                     When provided, unambiguous symbols mentioned in sentences
                     are resolved and anchors attached at extraction time.
                     Ambiguous names are reported, not guessed (I4).
        """
        requirements: list[Requirement] = []
        ambiguous_symbols: dict[str, list[str]] = {}

        for obs in observations:
            if obs.kind != "prompt":
                continue

            text = obs.text
            sentences = _split_sentences(text)

            for sentence_text, raw_start, _raw_end in sentences:
                if not _is_normative(sentence_text):
                    continue

                normalized = _normalize_statement(sentence_text)

                # Tighten span to stripped bounds: find where the stripped
                # text actually starts and ends in the original observation.
                stripped_start = text.find(sentence_text, raw_start)
                stripped_end = stripped_start + len(sentence_text)
                provenance = [Span(obs_id=obs.obs_id, start=stripped_start, end=stripped_end)]

                # Resolve anchors from the symbol table
                anchors: list[AnchorRef] = []
                if symbols:
                    anchors, ambiguous = _resolve_symbols(sentence_text, symbols)
                    for name in ambiguous:
                        if name not in ambiguous_symbols:
                            candidates = symbols.resolve_all(name)
                            ambiguous_symbols[name] = [c.symbol_path for c in candidates]

                requirements.append(
                    Requirement.create(
                        statement=normalized,
                        provenance=provenance,
                        extractor_version=VERSION,
                        anchors=anchors,
                    )
                )

        return ExtractionResult(
            requirements=requirements,
            ambiguous_symbols=ambiguous_symbols,
        )


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split text into (sentence, start_offset, end_offset) tuples.

    Uses a simple approach: split on sentence-ending punctuation,
    keeping track of char offsets. Offsets are unstripped: the returned
    sentence text is stripped, but start/end are the original boundaries
    so that text[start:end] is a valid (possibly whitespace-padded) slice.
    The caller tightens offsets to stripped bounds for provenance spans.
    """
    results: list[tuple[str, int, int]] = []
    current_start = 0

    for i, char in enumerate(text):
        if char in ".!?\n":
            segment_start = current_start
            segment_end = i + 1
            segment = text[segment_start:segment_end].strip()
            if segment:
                results.append((segment, segment_start, segment_end))
            current_start = i + 1

    # Handle remaining text (no trailing punctuation)
    remaining_start = current_start
    remaining_end = len(text)
    remaining = text[remaining_start:remaining_end].strip()
    if remaining:
        results.append((remaining, remaining_start, remaining_end))

    return results
