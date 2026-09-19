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
from intentrace.symbol import Ambiguous, NotFound, Resolved, SymbolTable

VERSION = "fake-v2"


# Normative markers (case-insensitive)
NORMATIVE_RE = re.compile(
    r"\b(must|should|never|always|don't|do not)\b",
    re.IGNORECASE,
)

# Token patterns for symbol matching: word, word.word, or Class.method
CLASS_METHOD_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]+)\.([a-z][A-Za-z0-9_]+)\b")
WORD_RE = re.compile(r"\b([a-z][A-Za-z0-9_]+)\b")

# A line starting with one of these begins a new unit (list/table/heading).
_LIST_MARKER_RE = re.compile(r"^(?:[-*+>|#]{1,6}|\d+[.)]|[A-Za-z][.)])(?=\s|$)")

# A line holding only a marker never ends a segment on its own ("1.", "-").
_MARKER_ONLY_RE = re.compile(r"^(?:[-*+>|#]{1,6}|\d+[.)]|[A-Za-z][.)])$")

# Abbreviations whose trailing period never ends a sentence when the text
# continues in lowercase (matched case-insensitively).
_ABBREVS = frozenset(
    {
        "e.g.",
        "i.e.",
        "etc.",
        "vs.",
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "st.",
        "sr.",
        "jr.",
        "fig.",
        "sec.",
        "eq.",
        "cf.",
        "al.",
        "no.",
        "vol.",
        "ch.",
        "pp.",
    }
)

# Closing quotes/brackets skipped when looking past a period. Includes the
# curly quotes pasted prose uses (U+201D RIGHT DOUBLE, U+2019 RIGHT SINGLE).
_CLOSERS = "\"')}]`\u201d\u2019"

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


def _resolve_symbols(
    sentence: str, symbols: SymbolTable
) -> tuple[list[AnchorRef], list[tuple[str, list[AnchorRef]]]]:
    """Resolve symbols mentioned in a sentence against the symbol table.

    Returns a tuple of (anchors, ambiguous). Only definitive
    (unambiguous) resolutions are returned as anchors. Each ambiguous
    entry carries the bare name plus the exact candidates that matched,
    so the report names what the sentence pointed at — guessing is an
    I4-class failure.
    """
    anchors: list[AnchorRef] = []
    ambiguous: list[tuple[str, list[AnchorRef]]] = []
    seen: set[str] = set()

    # Try Class.method first — resolve through the qualified path so the
    # named class wins over same-named methods elsewhere. Only when no
    # such class exists, fall back to the bare method name.
    for match in CLASS_METHOD_RE.finditer(sentence):
        class_name, method_name = match.groups()
        resolution = symbols.resolve_qualified(class_name, method_name)
        if isinstance(resolution, NotFound):
            resolution = symbols.resolve(method_name)
        if isinstance(resolution, Resolved) and method_name not in seen:
            anchors.append(resolution.anchor)
            seen.add(method_name)
        elif isinstance(resolution, Ambiguous) and method_name not in seen:
            ambiguous.append((method_name, list(resolution.candidates)))
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
            ambiguous.append((word, list(resolution.candidates)))
            seen.add(word)

    return anchors, ambiguous


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
                     Ambiguous names are reported, not guessed (I4). If any
                     mentioned symbol is ambiguous, the requirement stays
                     unanchored: a partially anchored requirement would
                     present unsettled code targets as settled.
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

                # Resolve anchors from the symbol table. All-or-nothing per
                # sentence: any ambiguity drops every anchor for the
                # requirement (see docstring).
                anchors: list[AnchorRef] = []
                if symbols:
                    anchors, ambiguous = _resolve_symbols(sentence_text, symbols)
                    for name, candidates in ambiguous:
                        if name not in ambiguous_symbols:
                            ambiguous_symbols[name] = [c.symbol_path for c in candidates]
                    if ambiguous:
                        anchors = []

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

    A period ends a sentence only when it is not inside a number or an
    abbreviation and is followed (past closing quotes/brackets) by
    whitespace or end of text — so `Class.method`, decimals, versions and
    `e.g.` survive intact. `!` and `?` always split. Newlines split at
    paragraph breaks and list/table/heading items, but join wrapped lines,
    so one pasted sentence stays one segment. A line holding only a list
    marker never ends a segment on its own.

    Offsets are unstripped: the returned sentence text is stripped, but
    start/end are the original boundaries so that text[start:end] is a
    valid (possibly whitespace-padded) slice. The caller tightens offsets
    to stripped bounds for provenance spans.
    """
    results: list[tuple[str, int, int]] = []
    current_start = 0
    n = len(text)

    def emit(end: int) -> None:
        nonlocal current_start
        segment = text[current_start:end].strip()
        if segment:
            results.append((segment, current_start, end))
        current_start = end

    def ends_with_abbrev(i: int) -> bool:
        window = text[max(0, i - 5) : i + 1].lower()
        return any(window.endswith(a) for a in _ABBREVS)

    def is_single_letter(i: int) -> bool:
        return text[i - 1].isalpha() and (i < 2 or not text[i - 2].isalpha())

    def period_ends(i: int) -> bool:
        # Decimals and versions never split: 3.12, v0.2.1.
        if i > 0 and i + 1 < n and text[i - 1].isdigit() and text[i + 1].isdigit():
            return False
        # A single letter plus period is an initial or abbreviation part.
        # Accepted trade-off: a sentence ending in an initial ("done by Z.")
        # merges with what follows instead of splitting.
        if i >= 1 and is_single_letter(i):
            return False
        # A known abbreviation continues in lowercase/digits: "e.g. foo".
        # At a real sentence end ("etc. Next") it still splits below.
        if ends_with_abbrev(i):
            j = i + 1
            while j < n and text[j] == " ":
                j += 1
            if j < n and (text[j].islower() or text[j].isdigit()):
                return False
        j = i + 1
        while j < n and text[j] in _CLOSERS:
            j += 1
        return j >= n or text[j].isspace()

    def line_of(pos: int) -> str:
        start = text.rfind("\n", 0, pos) + 1
        return text[start:pos]

    def next_line(pos: int) -> str:
        end = text.find("\n", pos + 1)
        return text[pos + 1 : end if end != -1 else n]

    i = 0
    while i < n:
        ch = text[i]
        if ch in "!?":
            emit(i + 1)
        elif ch == ".":
            if _MARKER_ONLY_RE.match(text[current_start : i + 1].strip()):
                pass  # "1." never ends a segment on its own
            elif period_ends(i):
                emit(i + 1)
        elif ch == "\n":
            line = line_of(i)
            following = next_line(i)
            if not line.strip() or not following.strip():
                emit(i + 1)  # paragraph break always splits
            elif _MARKER_ONLY_RE.match(line.strip()):
                pass  # a lone marker never ends a segment on its own
            elif _LIST_MARKER_RE.match(following.strip()):
                emit(i + 1)  # a new item starts: keep items as units
            elif line.rstrip().endswith(("!", "?")):
                emit(i + 1)
            elif line.rstrip().endswith("."):
                # Wrapped line: re-apply the period rules against a probe
                # of line + " " + next line start.
                stripped = line.rstrip()
                probe = stripped + " " + following.lstrip()
                if _period_probe_ends(probe, len(stripped) - 1):
                    emit(i + 1)
            # Otherwise the line wraps: join, do not split.
        i += 1

    # Handle remaining text (no trailing punctuation)
    remaining = text[current_start:].strip()
    if remaining:
        results.append((remaining, current_start, n))

    return results


def _period_probe_ends(probe: str, dot: int) -> bool:
    """Period rule for a wrapped line: probe is line + " " + next line start."""
    follower = probe[dot + 2 :][:1]
    if dot > 0 and probe[dot - 1].isdigit() and follower.isdigit():
        return False
    if dot >= 1 and probe[dot - 1].isalpha() and (dot < 2 or not probe[dot - 2].isalpha()):
        return False
    window = probe[max(0, dot - 5) : dot + 1].lower()
    return not (
        any(window.endswith(a) for a in _ABBREVS) and (follower.islower() or follower.isdigit())
    )
