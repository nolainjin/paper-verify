"""The verdict shown to the user must equal the verdict used for scoring
(audit CL-4 / JS-01 / P0-2). A split with no tie-break scores as Uncertain,
so the displayed consensus must be Uncertain too — not the first judge's.
"""

from paperverify.models import Citation, Fetched, Judgement, Verdict
from paperverify.score import score_citation


def _cite():
    return Citation(id=1, type="URL", ref="https://x.com", context="Smith (2017).", line=1)


def _fetched():
    return Fetched(id=1, status=200, title="Smith 2017", abstract="In 2017 Smith reported.")


def _j(name, verdict):
    return Judgement(judge=name, verdict=verdict, reason="r")


def test_split_no_tiebreak_consensus_is_uncertain():
    sc = score_citation(_cite(), _fetched(), [_j("a", Verdict.MATCH), _j("b", Verdict.MISMATCH)])
    # scored as Uncertain (15 pts) — the displayed consensus must agree.
    assert sc.breakdown["claim_match"] == 15
    assert sc.consensus is Verdict.UNCERTAIN


def test_split_with_tiebreak_consensus_matches_scored_verdict():
    sc = score_citation(
        _cite(), _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.PARTIAL)],
        tiebreak_judgement=_j("t", Verdict.MISMATCH),
    )
    # most-conservative across {Match, Partial, Mismatch} -> Mismatch (0 pts).
    assert sc.breakdown["claim_match"] == 0
    assert sc.consensus is Verdict.MISMATCH


def test_agreeing_judges_consensus_unchanged():
    sc = score_citation(_cite(), _fetched(), [_j("a", Verdict.MATCH), _j("b", Verdict.MATCH)])
    assert sc.consensus is Verdict.MATCH


def test_single_judge_consensus_is_that_verdict():
    sc = score_citation(_cite(), _fetched(), [_j("a", Verdict.PARTIAL)])
    assert sc.consensus is Verdict.PARTIAL
