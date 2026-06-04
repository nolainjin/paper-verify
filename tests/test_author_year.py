"""author/year credit must not fire on false positives (audit CL-3 / P0-4):
month names / sentence-initial capitalized words are not authors, and author
matching is token-exact (no substring), while real surname+year still scores.
"""

from paperverify.models import Citation, Fetched
from paperverify.score import _author_year_credit


def _cite(context):
    return Citation(id=1, type="URL", ref="https://x.com", context=context, line=1)


def test_month_name_not_treated_as_author():
    # "March" must not count as an author; only the year legitimately matches.
    pts, _ = _author_year_credit(
        _cite("In March 2017 the study ran."),
        Fetched(id=1, status=200, title="", abstract="the work ran in March of 2017"),
    )
    assert pts == 10  # year-only, not 20


def test_sentence_initial_word_not_treated_as_author():
    pts, _ = _author_year_credit(
        _cite("This study from 2019 is key."),
        Fetched(id=1, status=200, title="", abstract="This was published in 2019"),
    )
    assert pts == 10  # "This" is not an author -> year-only


def test_author_match_is_token_exact_not_substring_metadata():
    # "Lee" must not match the author "Leestma" by substring.
    pts, _ = _author_year_credit(
        _cite("Lee (2020) found an effect."),
        Fetched(id=1, status=200, authors=["John Leestma"], year=2020),
    )
    assert pts == 10  # year matches, author does not -> partial


def test_real_surname_and_year_still_full_credit_metadata():
    pts, _ = _author_year_credit(
        _cite("Smith et al. (2017) reported a gain."),
        Fetched(id=1, status=200, authors=["Smith"], year=2017),
    )
    assert pts == 20  # true positive preserved
