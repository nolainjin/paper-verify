"""Tests for the 100-point scoring rubric — no network, no API keys."""

from paperverify.models import Citation, Fetched, Judgement, Tier, Verdict
from paperverify.score import score_citation


def _cite(context="Smith et al. (2017) reported a gain.", ref="https://x.com"):
    return Citation(id=1, type="URL", ref=ref, context=context, line=1)


def _fetched(ok=True, title="Smith 2017 study", abstract="In 2017 Smith reported a gain."):
    return Fetched(id=1, status=200 if ok else 404, title=title, abstract=abstract)


def _j(name, verdict):
    return Judgement(judge=name, verdict=verdict, reason="r")


def test_perfect_match_is_A():
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.MATCH)],
    )
    # 20 url + 20 author/year + 50 claim + 10 cross-check = 100
    assert sc.score == 100.0
    assert sc.breakdown == {
        "url_accessible": 20,
        "author_year": 20,
        "claim_match": 50,
        "cross_check": 10,
    }
    assert sc.tier is Tier.A


def test_l1_scores_url_alive_only():
    live = score_citation(_cite(), _fetched(ok=True), [_j("a", Verdict.MISMATCH)], level="L1")
    dead = score_citation(_cite(), _fetched(ok=False), [_j("a", Verdict.MATCH)], level="L1")

    assert live.score == 100.0
    assert live.breakdown == {"url_alive": 100}
    assert live.judgements == []
    assert live.tier is Tier.A

    assert dead.score == 0.0
    assert dead.breakdown == {"url_alive": 0}
    assert dead.judgements == []
    assert dead.tier is Tier.F


def test_claim_points_per_verdict():
    expected = {
        Verdict.MATCH: 50,
        Verdict.PARTIAL: 25,
        Verdict.MISMATCH: 0,
        Verdict.INACCESSIBLE: 10,
    }
    for verdict, pts in expected.items():
        sc = score_citation(_cite(), _fetched(), [_j("a", verdict)])
        assert sc.breakdown["claim_match"] == pts


def test_url_not_accessible_zero_points():
    sc = score_citation(_cite(), _fetched(ok=False), [_j("a", Verdict.MATCH)])
    assert sc.breakdown["url_accessible"] == 0


def test_no_fetch_zero_url_points():
    sc = score_citation(_cite(), None, [_j("a", Verdict.MATCH)])
    assert sc.breakdown["url_accessible"] == 0
    assert sc.breakdown["author_year"] == 0


def test_cross_check_only_with_two_or_more_agreeing():
    # single judge -> no cross-check credit
    sc1 = score_citation(_cite(), _fetched(), [_j("a", Verdict.MATCH)])
    assert sc1.breakdown["cross_check"] == 0
    # two disagreeing -> no credit
    sc2 = score_citation(
        _cite(), _fetched(), [_j("a", Verdict.MATCH), _j("b", Verdict.PARTIAL)]
    )
    assert sc2.breakdown["cross_check"] == 0
    # two agreeing -> credit
    sc3 = score_citation(
        _cite(), _fetched(), [_j("a", Verdict.PARTIAL), _j("b", Verdict.PARTIAL)]
    )
    assert sc3.breakdown["cross_check"] == 10


def test_author_year_partial_credit_when_only_one_matches():
    # year present but author missing -> partial credit (10), not 0 (schema "3").
    sc = score_citation(
        _cite(context="Smith et al. (2017) said so."),
        _fetched(title="Anonymous", abstract="published in 2017"),
        [_j("a", Verdict.MATCH)],
    )
    assert sc.breakdown["author_year"] == 10

    # neither author nor year present -> no credit.
    sc2 = score_citation(
        _cite(context="Smith et al. (2017) said so."),
        _fetched(title="Anonymous", abstract="unrelated text 1999 Jones"),
        [_j("a", Verdict.MATCH)],
    )
    assert sc2.breakdown["author_year"] == 0


def test_tier_thresholds():
    assert Tier.from_score(100) is Tier.A
    assert Tier.from_score(90) is Tier.A
    assert Tier.from_score(89) is Tier.B
    assert Tier.from_score(70) is Tier.B
    assert Tier.from_score(69) is Tier.C
    assert Tier.from_score(50) is Tier.C
    assert Tier.from_score(49) is Tier.F
    assert Tier.from_score(0) is Tier.F


def test_no_judgements_defaults_inaccessible_claim_points():
    sc = score_citation(_cite(), _fetched(), [])
    assert sc.breakdown["claim_match"] == 10  # Inaccessible default


def test_mismatch_inaccessible_url_gives_F():
    # 404 + mismatch + author/year absent from a non-empty source + single judge
    # => 0 points => F. (An empty source is "metadata unavailable" => neutral 10.)
    sc = score_citation(
        _cite(context="no author here just text"),
        _fetched(ok=False, title="Some Page", abstract="unrelated content with no match"),
        [_j("a", Verdict.MISMATCH)],
    )
    assert sc.score == 0.0
    assert sc.tier is Tier.F


def test_empty_source_author_year_is_neutral_not_punished():
    # No metadata at all to compare against -> neutral partial credit (10),
    # not a misleading 0 (schema "3" lenient author/year rule).
    sc = score_citation(
        _cite(context="Smith et al. (2017) said so."),
        _fetched(ok=False, title="", abstract=""),
        [_j("a", Verdict.MISMATCH)],
    )
    assert sc.breakdown["author_year"] == 10
