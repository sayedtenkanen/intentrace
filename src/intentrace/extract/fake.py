"""FakeExtractor — deterministic, rule-based requirement extraction.

No model in the loop. Considers only prompt observations, splits into
sentences, and emits requirements for sentences containing normative markers.
Symbols are resolved against a provided symbol table at extraction time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from intentrace.models import AnchorRef, Observation, Requirement, Span

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


def _resolve_symbols(sentence: str, symbols: dict[str, AnchorRef]) -> list[AnchorRef]:
    """Resolve symbols mentioned in a sentence against the symbol table."""
    anchors: list[AnchorRef] = []
    seen: set[str] = set()

    # Try Class.method first
    for match in CLASS_METHOD_RE.finditer(sentence):
        _class_name, method_name = match.groups()
        if method_name in symbols and method_name not in seen:
            anchors.append(symbols[method_name])
            seen.add(method_name)

    # Try standalone words
    for match in WORD_RE.finditer(sentence):
        word = match.group(1)
        if word not in SKIP_WORDS and word not in seen and word in symbols:
            anchors.append(symbols[word])
            seen.add(word)

    return anchors


def _normalize_statement(text: str) -> str:
    """Normalize whitespace in a statement."""
    return " ".join(text.split())


class FakeExtractor:
    """Deterministic, rule-based extractor for Slice 1."""

    version: str = VERSION

    def extract(
        self,
        observations: Iterable[Observation],
        symbols: dict[str, AnchorRef] | None = None,
    ) -> list[Requirement]:
        """Extract requirements from prompt observations.

        Args:
            observations: The observations to extract from.
            symbols: Optional mapping of symbol names to their AnchorRefs.
                     When provided, symbols mentioned in sentences are resolved
                     and anchors attached at extraction time.
        """
        requirements: list[Requirement] = []

        for obs in observations:
            if obs.kind != "prompt":
                continue

            text = obs.text
            sentences = _split_sentences(text)

            for sentence_text, start, end in sentences:
                if not _is_normative(sentence_text):
                    continue

                normalized = _normalize_statement(sentence_text)
                provenance = [Span(obs_id=obs.obs_id, start=start, end=end)]

                # Resolve anchors from the symbol table
                anchors: list[AnchorRef] = []
                if symbols:
                    anchors = _resolve_symbols(sentence_text, symbols)

                requirements.append(
                    Requirement.create(
                        statement=normalized,
                        provenance=provenance,
                        extractor_version=VERSION,
                        anchors=anchors,
                    )
                )

        return requirements


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split text into (sentence, start_offset, end_offset) tuples.

    Uses a simple approach: split on sentence-ending punctuation,
    keeping track of char offsets. Offsets refer to the unstripped
    segment boundaries so that text[start:end] is a valid slice of
    the original text.
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
