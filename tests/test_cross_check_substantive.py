"""M1: cross-check credit (+10) requires *genuine* multi-judge agreement.

When one primary judge is INACCESSIBLE (an availability signal, not a claim
judgement) and only a single substantive judge remains, a tie-break can resolve
the claim verdict — but that is one judge's opinion, not a cross-checked
consensus. The old code awarded +10 cross-check on any tie-break resolution,
so a lone substantive judge looked corroborated. Credit must require >=2
non-INACCESSIBLE judges agreeing on the winning verdict (audit M1).
"""

from paperverify.models import Citation, Fetched, Judgement, Verdict
from paperverify.score import score_citation


def _cite():
    return Citation(id=1, type="URL", ref="https://x.com", context="Smith (2017).", line=1)


def _fetched():
    return Fetched(id=1, status=200, title="Smith 2017", abstract="In 2017 Smith reported.")


def _j(name, verdict):
    return Judgement(judge=name, verdict=verdict, reason="r")


def test_single_substantive_judge_with_tiebreak_no_cross_check():
    # [PARTIAL, INACCESSIBLE] + tiebreak PARTIAL: only one substantive judge,
    # so no genuine cross-check — credit must be 0 (was spuriously 10).
    sc = score_citation(
        _cite(), _fetched(),
        [_j("a", Verdict.PARTIAL), _j("b", Verdict.INACCESSIBLE)],
        tiebreak_judgement=_j("t", Verdict.PARTIAL),
    )
    assert sc.consensus is Verdict.PARTIAL
    assert sc.breakdown["cross_check"] == 0


def test_two_substantive_agreeing_judges_keep_cross_check():
    # Two substantive judges both PARTIAL (+ a 3rd INACCESSIBLE): genuine
    # agreement -> credit stays 10.
    sc = score_citation(
        _cite(), _fetched(),
        [_j("a", Verdict.PARTIAL), _j("b", Verdict.PARTIAL), _j("c", Verdict.INACCESSIBLE)],
        tiebreak_judgement=_j("t", Verdict.MISMATCH),
    )
    assert sc.consensus is Verdict.PARTIAL
    assert sc.breakdown["cross_check"] == 10


def test_tiebreak_resolved_two_substantive_winners_keep_cross_check():
    # Split [MATCH, MISMATCH] + tiebreak MATCH -> winner MATCH with two
    # substantive judges (a + tiebreak) supporting it... but the tiebreak is the
    # arbiter, not a peer. Genuine cross-check requires two *primary* substantive
    # judges agreeing; here only one primary (a) holds MATCH -> no credit.
    sc = score_citation(
        _cite(), _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.MISMATCH)],
        tiebreak_judgement=_j("t", Verdict.MATCH),
    )
    assert sc.consensus is Verdict.MATCH
    assert sc.breakdown["cross_check"] == 0
