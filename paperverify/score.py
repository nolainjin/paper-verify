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

# Most-conservative ordering for consensus after a tie-break (lower = worse).
_VERDICT_RANK = {
    Verdict.MISMATCH: 0,
    Verdict.INACCESSIBLE: 1,
    Verdict.UNCERTAIN: 2,
    Verdict.PARTIAL: 3,
    Verdict.MATCH: 4,
}

_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_AUTHOR_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")


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
    ctx_authors = [a for a in _AUTHOR_RE.findall(ctx)]

    # Structured metadata path (Crossref / arXiv / NCBI) — authoritative.
    has_structured = bool(fetched.authors) or fetched.year is not None
    if has_structured:
        year_ok = (
            fetched.year is not None
            and bool(ctx_years)
            and str(fetched.year) in ctx_years
        )
        meta_authors = " ".join(fetched.authors).lower()
        author_ok = bool(ctx_authors) and any(
            a.lower() in meta_authors for a in ctx_authors
        )
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

    year_ok = any(y in source for y in ctx_years) if ctx_years else False
    author_ok = any(a.lower() in source for a in ctx_authors) if ctx_authors else False
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
    supplied, take the most-conservative across all verdicts (incl. the
    tie-break) as consensus and award cross-check credit (the run reached a
    decision); with no tie-break, the effective verdict is ``Uncertain`` and
    cross-check stays 0.
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
        consensus = min(all_verdicts, key=lambda v: _VERDICT_RANK[v])
        return consensus, True, "resolved by tie-break (most-conservative)"
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
    )
