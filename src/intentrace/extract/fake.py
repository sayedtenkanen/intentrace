"""FakeExtractor — deterministic, rule-based requirement extraction.

No model in the loop. Considers only prompt observations, splits into
sentences, and emits requirements for sentences containing normative markers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from intentrace.models import Observation, Requirement, Span

VERSION = "fake-v1"

# Normative markers (case-insensitive)
NORMATIVE_RE = re.compile(
    r"\b(must|should|never|always|don't|do not)\b",
    re.IGNORECASE,
)

# Token patterns for symbol matching: word, word.word, or Class.method
SYMBOL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*\.[a-z][A-Za-z0-9_]*)\b")
WORD_RE = re.compile(r"\b([a-z][A-Za-z0-9_]+)\b")


def _is_normative(sentence: str) -> bool:
    """Check if a sentence contains a normative marker."""
    return bool(NORMATIVE_RE.search(sentence))


def _extract_symbol_tokens(sentence: str) -> list[str]:
    """Extract potential symbol tokens from a sentence."""
    # Try Class.method first, then individual words
    symbols: list[str] = []
    for match in SYMBOL_RE.finditer(sentence):
        symbols.append(match.group(1))
    # Also collect single lowercase words that might be function names
    for match in WORD_RE.finditer(sentence):
        word = match.group(1)
        # Skip common English words
        if word not in {"the", "and", "for", "that", "this", "with", "from", "are", "was"}:
            symbols.append(word)
    return symbols


def _normalize_statement(text: str) -> str:
    """Normalize whitespace in a statement."""
    return " ".join(text.split())


class FakeExtractor:
    """Deterministic, rule-based extractor for Slice 1."""

    version: str = VERSION

    def extract(self, observations: Iterable[Observation]) -> list[Requirement]:
        """Extract requirements from prompt observations."""
        requirements: list[Requirement] = []

        for obs in observations:
            if obs.kind != "prompt":
                continue

            text = obs.text
            # Split into sentences (rough: split on sentence boundaries)
            sentences = _split_sentences(text)

            for sentence_text, start, end in sentences:
                if not _is_normative(sentence_text):
                    continue

                normalized = _normalize_statement(sentence_text)
                provenance = [Span(obs_id=obs.obs_id, start=start, end=end)]
                requirements.append(
                    Requirement.create(
                        statement=normalized,
                        provenance=provenance,
                        extractor_version=VERSION,
                    )
                )

        return requirements


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split text into (sentence, start_offset, end_offset) tuples.

    Uses a simple approach: split on sentence-ending punctuation,
    keeping track of char offsets.
    """
    results: list[tuple[str, int, int]] = []
    current_start = 0

    for i, char in enumerate(text):
        if char in ".!?\n":
            segment = text[current_start : i + 1].strip()
            if segment:
                # Find the actual start of this segment in the original text
                actual_start = text.find(segment, current_start)
                results.append((segment, actual_start, actual_start + len(segment)))
            current_start = i + 1

    # Handle remaining text (no trailing punctuation)
    remaining = text[current_start:].strip()
    if remaining:
        actual_start = text.find(remaining, current_start)
        results.append((remaining, actual_start, actual_start + len(remaining)))

    return results
