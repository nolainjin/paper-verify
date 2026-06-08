"""H2: the dependency-free KeywordJudge does token overlap only, so it cannot
tell support from contradiction. A claim like "anxiety **increased** 40% in
2019" against a source saying "anxiety **decreased**. 40 percent. 2019" shares
all the key tokens (40, 2019, anxiety) and so was scored a full Match — the
no-API-key default path, which the report frames as a usable Tier B.

The conservative fix: when the claim and the source disagree on *direction*
(an antonym pair like increase/decrease, rise/fall) or on *negation*, the
keyword heuristic must not assert Match — it clamps to Partial (the figure may
agree while the meaning is inverted). Genuinely supportive sources, with no
polarity conflict, still score Match.
"""

from paperverify.judge import KeywordJudge
from paperverify.models import Verdict


def test_increase_vs_decrease_not_match():
    j = KeywordJudge().evaluate(
        "anxiety increased 40% in 2019",
        "anxiety decreased. 40 percent. 2019",
    )
    assert j.verdict is not Verdict.MATCH
    assert j.verdict is Verdict.PARTIAL


def test_rose_vs_fell_not_match():
    j = KeywordJudge().evaluate(
        "depression rates rose sharply among teenagers",
        "depression rates fell sharply among teenagers",
    )
    assert j.verdict is not Verdict.MATCH


def test_negation_conflict_not_match():
    j = KeywordJudge().evaluate(
        "the program improved retention among students",
        "the program did not improve retention among students",
    )
    assert j.verdict is not Verdict.MATCH


def test_supportive_source_still_match():
    # Same direction, no negation conflict -> still Match.
    j = KeywordJudge().evaluate(
        "anxiety increased 40 percent in 2019 among adults",
        "in 2019 anxiety increased by 40 percent among adults overall",
    )
    assert j.verdict is Verdict.MATCH


def test_no_polarity_words_unaffected():
    # No directional / negation words on either side -> heuristic unchanged.
    j = KeywordJudge().evaluate(
        "Smith reported a cognitive gain among adults",
        "Smith reported a cognitive gain among adults in the study",
    )
    assert j.verdict is Verdict.MATCH
