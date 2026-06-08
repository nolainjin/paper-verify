"""100-point scoring rubric (SKILL §4).

    L1 URL alive       100   HTTP 2xx (50 when soft-404 suspect, 0 unreachable)
    URL accessible      20   HTTP 2xx (L2/L3)
    author/year match   20   author + year align; 10 partial; neutral when metadata absent
    claim match         50   Match=50 / Partial=25 / Uncertain=15 / Mismatch=0 / Inaccessible=10
    cross-check agree   10   judges agree (incl. after a tie-break)

Tier thresholds (Tier.from_score): A >=90, B >=70, C >=50, F <50.
"""

from __future__ import annotations

import re

from .models import Citation, Fetched, Judgement, ScoredCitation, Verdict

_CLAIM_POINTS = {
    Verdict.MATCH: 50,
    Verdict.PARTIAL: 25,
    Verdict.UNCERTAIN: 15,
    Verdict.MISMATCH: 0,
    Verdict.INACCESSIBLE: 10,
}

_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_AUTHOR_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")
_WORD_RE = re.compile(r"[a-z]+")

# Capitalized tokens that look like author surnames but are not, in citation
# context: month names and very common sentence-initial / function words. Kept
# conservative to avoid excluding real surnames (audit CL-3).
_AUTHOR_STOPWORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "the", "this", "that", "these", "those", "from", "and", "but", "for", "with",
    "when", "while", "their", "there", "here", "what", "which", "who", "how", "why",
    "our", "his", "her", "its", "however", "although", "because", "during",
    "between", "among", "also", "thus", "hence", "moreover", "furthermore",
    "study", "studies", "table", "figure", "section", "chapter", "data",
    "results", "result", "method", "methods", "see", "note", "abstract",
}


def _candidate_authors(ctx: str) -> list[str]:
    """Capitalized context tokens that plausibly name an author (CL-3)."""
    return [a for a in _AUTHOR_RE.findall(ctx) if a.lower() not in _AUTHOR_STOPWORDS]


def _author_year_credit(citation: Citation, fetched: Fetched | None) -> tuple[int, str]:
    """Score the author/year slot, preferring structured metadata.

    Returns ``(points, note)``. Full match = 20, year-only or author-only = 10,
    none = 0. When **no** metadata is available to compare against (no structured
    authors/year *and* an empty scraped source) the slot is treated as neutral —
    a partial 10 with a "metadata unavailable" note — rather than a misleading 0
    that would tank the score for a source we simply could not introspect.
    """
    if fetched is None:
        return 0, "no fetch result"

    ctx = citation.context
    ctx_years = set(_YEAR_RE.findall(ctx))
    ctx_authors = _candidate_authors(ctx)

    # Structured metadata path (Crossref / arXiv / NCBI) — authoritative.
    has_structured = bool(fetched.authors) or fetched.year is not None
    if has_structured:
        year_ok = (
            fetched.year is not None
            and bool(ctx_years)
            and str(fetched.year) in ctx_years
        )
        # Token-exact author match (no substring: "Lee" must not match "Leestma").
        meta_tokens = set(_WORD_RE.findall(" ".join(fetched.authors).lower()))
        author_ok = any(a.lower() in meta_tokens for a in ctx_authors)
        if year_ok and author_ok:
            return 20, "author+year match (metadata)"
        if year_ok or author_ok:
            return 10, "partial author/year match (metadata)"
        return 0, "author/year mismatch (metadata)"

    # Fallback: fuzzy match against scraped HTML title + abstract.
    source = f"{fetched.title} {fetched.abstract}".lower()
    if not source.strip():
        # No metadata at all to compare — do not punish; neutral partial credit.
        return 10, "metadata unavailable — author/year not scored"

    source_tokens = set(_WORD_RE.findall(source))
    year_ok = any(y in source for y in ctx_years) if ctx_years else False
    author_ok = any(a.lower() in source_tokens for a in ctx_authors)
    if year_ok and author_ok:
        return 20, "author+year match (text)"
    if year_ok or author_ok:
        return 10, "partial author/year match (text)"
    return 0, "author/year not found in source"


def _consensus_verdict(
    judgements: list[Judgement], tiebreak: Judgement | None
) -> tuple[Verdict, bool, str]:
    """Resolve the consensus verdict used for the 50-pt claim slot.

    Returns ``(verdict, cross_check_agree, note)``.

    Single judge: that verdict, no cross-check credit. 2+ judges that agree:
    agreement, 10-pt credit. 2+ that disagree: if a ``tiebreak`` verdict is
    supplied, take the **majority** verdict across all judgements plus the
    tie-break (``Inaccessible`` excluded from the claim axis — it is an
    availability signal, not a claim judgement) and award cross-check credit;
    the tie-break judge arbitrates a genuine tie between distinct verdicts. With
    no tie-break, the effective verdict is ``Uncertain`` and cross-check stays 0.
    """
    if not judgements:
        return Verdict.INACCESSIBLE, False, ""
    if len(judgements) == 1:
        return judgements[0].verdict, False, ""

    verdicts = {j.verdict for j in judgements}
    if len(verdicts) == 1:
        return judgements[0].verdict, True, "judges agree"

    # Disagreement.
    if tiebreak is not None:
        all_verdicts = [j.verdict for j in judgements] + [tiebreak.verdict]
        # INACCESSIBLE is an availability signal, not a claim judgement, so it
        # must not become the claim verdict when the reachable judges agree
        # (audit P1-4 / JS-02). Score the claim on the substantive verdicts only.
        substantive = [v for v in all_verdicts if v is not Verdict.INACCESSIBLE]
        pool = substantive or all_verdicts  # all-inaccessible: fall back to all
        # Majority vote — the most-supported verdict, not the single most
        # pessimistic one (the old min() let one dissenting vote drag the score).
        counts: dict[Verdict, int] = {}
        for v in pool:
            counts[v] = counts.get(v, 0) + 1
        top = max(counts.values())
        winners = [v for v in pool if counts[v] == top]
        resolved = winners[0] if len(set(winners)) == 1 else tiebreak.verdict
        note = (
            "resolved by majority (tie-break included)"
            if len(set(winners)) == 1
            else "resolved by tie-break arbiter"
        )
        # Cross-check credit (+10) is *genuine multi-judge agreement*, not the
        # mere fact that a tie-break produced an answer. Require >=2 substantive
        # (non-INACCESSIBLE) *primary* judges holding the resolved verdict; a lone
        # substantive judge that a tie-break sided with is not corroborated (M1).
        primary_agree = sum(
            1
            for j in judgements
            if j.verdict is not Verdict.INACCESSIBLE and j.verdict is resolved
        )
        if primary_agree >= 2:
            return resolved, True, note
        return resolved, False, note + " (single substantive judge — no cross-check)"
    return Verdict.UNCERTAIN, False, "judges split, no tie-break — flagged for human review"


def score_citation(
    citation: Citation,
    fetched: Fetched | None,
    judgements: list[Judgement],
    *,
    level: str = "L2",
    tiebreak_judgement: Judgement | None = None,
) -> ScoredCitation:
    """Apply the 100-point rubric and return a :class:`ScoredCitation`.

    Args:
        tiebreak_judgement: optional 3rd-judge verdict used only to break a tie
            when 2+ primary judges disagree (see :func:`_consensus_verdict`).
    """
    breakdown: dict[str, int] = {}
    level = level.upper()

    if level == "L1":
        if fetched is not None and fetched.ok:
            # A metadata hit can be 2xx (the API answered) while the human-facing
            # landing URL is dead. When the landing status was actually probed
            # (not None) and is non-2xx, the link is dead regardless of the
            # metadata status — "metadata exists" != "the URL resolves" (H1).
            landing = fetched.landing_status
            if landing is not None and not (200 <= landing < 300):
                breakdown["url_alive"] = 0
            else:
                breakdown["url_alive"] = 50 if fetched.soft_404_suspect else 100
        else:
            breakdown["url_alive"] = 0
        return ScoredCitation(
            citation=citation,
            fetched=fetched,
            judgements=[],
            score=float(sum(breakdown.values())),
            breakdown=breakdown,
        )

    # 1) URL accessible — 20 pts for HTTP 2xx.
    breakdown["url_accessible"] = 20 if (fetched is not None and fetched.ok) else 0

    # 2) Author/year match — 20 / 10 / 0 pts (structured metadata preferred).
    breakdown["author_year"], _ay_note = _author_year_credit(citation, fetched)

    # 3) Claim match — up to 50 pts, driven by the consensus verdict.
    consensus, agree, _cc_note = _consensus_verdict(judgements, tiebreak_judgement)
    breakdown["claim_match"] = _CLAIM_POINTS[consensus]

    # 4) Cross-check agreement — 10 pts only on genuine agreement (incl. post-tiebreak).
    breakdown["cross_check"] = 10 if agree else 0

    score = float(sum(breakdown.values()))
    return ScoredCitation(
        citation=citation,
        fetched=fetched,
        judgements=list(judgements),
        score=score,
        breakdown=breakdown,
        effective_verdict=consensus,
    )
