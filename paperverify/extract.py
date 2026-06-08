"""Citation extraction from Markdown / plain-text documents.

Ports the regex patterns from the original ``extract-citations.py`` into a
single :func:`extract` function that returns :class:`~paperverify.models.Citation`
objects, deduped by ``(type, ref)``, each carrying ~100 chars of surrounding
context and a 1-based line number.
"""

from __future__ import annotations

import re

from .models import Citation

# Reference patterns, in priority order. The first pattern that owns a given
# substring wins; later patterns dedupe against earlier ones via (type, ref).
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://[^\s)\]\"<>]+(?<![.,;:!?])")),
    ("DOI", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)),
    ("PMC", re.compile(r"\bPMC\d+\b")),
    ("PMID", re.compile(r"\bPMID:?\s*\d+\b", re.I)),
    ("arXiv", re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}", re.I)),
]

CTX_CHARS = 100  # characters of context kept on each side of the match

# A DOI carried inside a (dx.)doi.org URL — used to dedupe a bare DOI elsewhere
# in the document against its own resolver URL (same physical source, CL-1/M2).
_DOI_IN_URL_RE = re.compile(
    r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)$", re.I
)


def _line_index(text: str) -> list[int]:
    """Return the start offset of every line (offset 0 starts line 1)."""
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _pos_to_line(line_starts: list[int], pos: int) -> int:
    """Map a character offset to its 1-based line number (binary search)."""
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def extract(text: str) -> list[Citation]:
    """Extract all citations from ``text``, deduped by ``(type, ref)``.

    Args:
        text: full document contents.

    Returns:
        A list of :class:`Citation` in extraction order (URLs first, then
        DOI / PMC / PMID / arXiv), with ids assigned 1..N.
    """
    line_starts = _line_index(text)
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    cid = 0
    url_spans: list[tuple[int, int]] = []  # spans claimed by URL matches

    for type_name, pat in PATTERNS:
        for m in pat.finditer(text):
            if type_name == "URL":
                url_spans.append((m.start(), m.end()))
            # A DOI/PMC/PMID/arXiv sitting inside an already-matched URL is the
            # same physical source (fetch._metadata_for routes URL-borne ids),
            # so don't count it as a second citation (CL-1).
            elif any(s <= m.start() and m.end() <= e for s, e in url_spans):
                continue
            ref = m.group(0).rstrip(".,;:!?)")
            key = (type_name, ref.lower())
            if key in seen:
                continue
            seen.add(key)
            # A (dx.)doi.org URL *is* a DOI resolver: pre-claim the embedded DOI
            # so the same DOI written bare elsewhere dedupes against this URL
            # rather than counting as a second citation (M2). A *different* bare
            # DOI keeps its own key and is unaffected.
            if type_name == "URL":
                doi_m = _DOI_IN_URL_RE.match(ref)
                if doi_m:
                    seen.add(("DOI", doi_m.group(1).rstrip(".,;:!?)").lower()))
            cid += 1
            start = max(0, m.start() - CTX_CHARS)
            end = min(len(text), m.end() + CTX_CHARS)
            context = text[start:end].replace("\n", " ").strip()
            out.append(
                Citation(
                    id=cid,
                    type=type_name,
                    ref=ref,
                    context=context,
                    line=_pos_to_line(line_starts, m.start()),
                )
            )
    return out
