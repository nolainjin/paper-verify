"""Tests for the P1 robustness backlog (audit 2026-06-04, 01_BACKLOG.md).

Each section is one backlog item. All tests are network-free: the HTTP and
metadata layers are monkeypatched. Items covered here:

    P1-3  soft-404 markers narrowed (no false positive on legit academic pages)
    P1-5  keyword judge Match clamped + year/number guard
    P1-8  sources._get read cap (MAX_BYTES)
    P1-1  main HTTP path retry/backoff on transient errors
    P1-2  Wayback fallback uses a timestamped snapshot URL
    P1-4  tiebreak consensus is majority/arbiter, INACCESSIBLE separated
    P1-6  charset parsing robust to quotes / meta charset / BOM
    P1-9  metadata API hosts are rate-limited too
"""

import paperverify.fetch as fetch_mod
from paperverify import sources
from paperverify.fetch import _detect_soft_404


# ---------------------------------------------------------------------------
# P1-3 — soft-404 markers narrowed (CL-6 / FR-07 / SEC-06)
# ---------------------------------------------------------------------------


def _long_body(extra: str = "") -> str:
    # A plausible, content-rich academic page body (well over _MIN_BODY_CHARS).
    return (
        "This article reports a randomized controlled trial of the intervention. "
        "We measured outcomes across the cohort and analyzed the results in detail. "
    ) * 6 + extra


def test_soft_404_not_flagged_on_legit_page_mentioning_error_word():
    # "error" appears in normal scientific prose (standard error, error bars,
    # type I error). A long, healthy page must NOT be flagged as a soft-404.
    body = _long_body("We report the standard error of the mean and type I error rate.")
    assert not _detect_soft_404("Standard Error in Clinical Trials", body,
                                "http://j.org/article/1", "http://j.org/article/1")


def test_soft_404_not_flagged_on_legit_page_mentioning_404_token():
    # "404" appears as a wavelength / measurement, not an HTTP status.
    body = _long_body("Absorbance peaked at 404 nm under the assay conditions.")
    assert not _detect_soft_404("Spectroscopy at 404 nm", body,
                                "http://j.org/article/2", "http://j.org/article/2")


def test_soft_404_still_flags_specific_not_found_phrase():
    # A genuine error stub with a specific phrase is still caught.
    assert _detect_soft_404("Page Not Found", "x" * 400,
                            "http://a/deep", "http://a/deep")
    assert _detect_soft_404("Home", "The page you requested could not be found. " + "x" * 400,
                            "http://a/deep", "http://a/deep")


def test_soft_404_still_flags_korean_not_found_phrase():
    assert _detect_soft_404("홈", "요청하신 페이지를 찾을 수 없습니다. " + "x" * 400,
                            "http://a/deep", "http://a/deep")


# ---------------------------------------------------------------------------
# P1-5 — keyword judge: clamp Match when a claim's year/number is absent
#        from the source (JS-03 / CL-9). KeywordJudge is dependency-free and
#        only does token overlap, so it cannot verify quantitative agreement;
#        a Match on a claim whose specific year/figure is missing is unsafe.
# ---------------------------------------------------------------------------

from paperverify.judge import KeywordJudge  # noqa: E402
from paperverify.models import Verdict  # noqa: E402


def test_keyword_match_clamped_to_partial_when_claim_year_absent():
    judge = KeywordJudge()
    # High word overlap, but the claim's year (2017) is not in the source.
    claim = "Smith reported a substantial cognitive gain in adults in 2017."
    source = "Smith reported a substantial cognitive gain in adults."
    j = judge.evaluate(claim, source)
    assert j.verdict is not Verdict.MATCH
    assert j.verdict is Verdict.PARTIAL


def test_keyword_match_clamped_to_partial_when_claim_number_absent():
    judge = KeywordJudge()
    # The cited figure (37 percent) is the whole point and is missing.
    claim = "The intervention improved retention by 37 percent across the cohort."
    source = "The intervention improved retention across the cohort overall."
    j = judge.evaluate(claim, source)
    assert j.verdict is Verdict.PARTIAL


def test_keyword_match_kept_when_year_and_number_present():
    judge = KeywordJudge()
    claim = "Smith reported a 37 percent gain in 2017 among adults."
    source = "In 2017 Smith reported a 37 percent gain among adults in the study."
    j = judge.evaluate(claim, source)
    assert j.verdict is Verdict.MATCH


def test_keyword_match_kept_when_claim_has_no_year_or_number():
    judge = KeywordJudge()
    claim = "Smith reported a substantial cognitive gain among adults."
    source = "Smith reported a substantial cognitive gain among adults in the study."
    j = judge.evaluate(claim, source)
    assert j.verdict is Verdict.MATCH
