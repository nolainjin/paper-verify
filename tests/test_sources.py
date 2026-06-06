"""Tests for metadata APIs, the fetch fallback chain, soft-404, Uncertain, and
tie-break — all network-free (sources / fetch are monkeypatched with fixtures).
"""

import paperverify.fetch as fetch_mod
from paperverify import sources
from paperverify.fetch import _detect_soft_404, fetch
from paperverify.models import Citation, Fetched, Judgement, Verdict
from paperverify.score import score_citation


# ---------------------------------------------------------------------------
# Feature 1 — metadata APIs + explicit, observable fallback chain
# ---------------------------------------------------------------------------


def _doi_cite():
    return Citation(id=7, type="DOI", ref="10.1145/3571730", context="Ji 2023 survey.", line=1)


def test_doi_uses_crossref_metadata_source(monkeypatch):
    meta = {"title": "Survey", "authors": ["Ziwei Ji"], "year": 2023, "abstract": "abstract text"}
    monkeypatch.setattr(sources, "fetch_doi_metadata", lambda doi: meta)
    # Reachability status-check path: make _open succeed cheaply.
    monkeypatch.setattr(fetch_mod, "_open", lambda url, method: (200, url, "text/html", b""))

    f = fetch(_doi_cite(), level="L2")
    assert f.source == "crossref"
    assert f.title == "Survey"
    assert f.authors == ["Ziwei Ji"]
    assert f.year == 2023
    assert f.ok


def test_metadata_failure_falls_back_to_http_source(monkeypatch):
    # Metadata API "fails" (returns None) -> chain falls through to HTTP.
    monkeypatch.setattr(sources, "fetch_doi_metadata", lambda doi: None)

    def fake_fetch_one(url, level):
        return Fetched(id=0, status=200, title="HTML title", abstract="x" * 400, source="http")

    monkeypatch.setattr(fetch_mod, "_fetch_one", fake_fetch_one)

    f = fetch(_doi_cite(), level="L2")
    assert f.source == "http"  # visible the API did NOT serve it
    assert f.ok


def test_metadata_raises_is_swallowed_and_falls_back_to_http(monkeypatch):
    # Even if the metadata function RAISES, the run must not crash; sources.py
    # swallows internally, but guard the chain too by raising from the API layer.
    def boom(doi):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(sources, "fetch_doi_metadata", boom)
    monkeypatch.setattr(
        fetch_mod,
        "_fetch_one",
        lambda url, level: Fetched(id=0, status=200, title="t", abstract="y" * 400, source="http"),
    )

    f = fetch(_doi_cite(), level="L2")
    assert f.source == "http"
    assert f.ok


def test_whole_chain_fails_is_inaccessible_source_none(monkeypatch):
    import urllib.error

    monkeypatch.setattr(sources, "fetch_doi_metadata", lambda doi: None)

    def fail(url, level):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(fetch_mod, "_fetch_one", fail)
    # Keep the test network-free: pretend the page was archived so the chain
    # exercises the archive branch without a live Availability API call, then
    # the (mocked) archive fetch also fails -> source "none".
    monkeypatch.setattr(
        fetch_mod, "_archive_url", lambda url: "https://web.archive.org/web/2/" + url
    )

    f = fetch(_doi_cite(), level="L2")
    assert f.source == "none"
    assert f.status == 0
    assert f.error
    assert not f.ok  # scored as Inaccessible — no invented metadata


def test_crossref_parser_normalizes(monkeypatch):
    import json

    payload = json.dumps(
        {
            "message": {
                "title": ["Attention Is All You Need"],
                "author": [{"family": "Vaswani", "given": "Ashish"}],
                "issued": {"date-parts": [[2017, 6, 12]]},
                "abstract": "<jats:p>We propose the Transformer.</jats:p>",
            }
        }
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)

    m = sources.fetch_doi_metadata("10.0/x")
    assert m["title"] == "Attention Is All You Need"
    assert m["authors"] == ["Ashish Vaswani"]
    assert m["year"] == 2017
    assert "<jats:p>" not in m["abstract"]
    assert "Transformer" in m["abstract"]


def test_ncbi_parser_normalizes(monkeypatch):
    import json

    payload = json.dumps(
        {
            "result": {
                "uids": ["23456789"],
                "23456789": {
                    "uid": "23456789",
                    "title": "Hospital volume study.",
                    "authors": [{"name": "Sharma A"}, {"name": "Mendez E"}],
                    "pubdate": "2013 May 15",
                },
            }
        }
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)

    m = sources.fetch_pmid_metadata("PMID: 23456789")
    assert m["title"].startswith("Hospital volume")
    assert m["authors"] == ["Sharma A", "Mendez E"]
    assert m["year"] == 2013


def test_arxiv_parser_normalizes(monkeypatch):
    atom = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry>"
        "<title>Attention Is All You Need</title>"
        "<summary>We propose the Transformer.</summary>"
        "<published>2017-06-12T00:00:00Z</published>"
        "<author><name>Ashish Vaswani</name></author>"
        "<author><name>Noam Shazeer</name></author>"
        "</entry></feed>"
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": atom)

    m = sources.fetch_arxiv_metadata("arXiv:1706.03762")
    assert m["title"] == "Attention Is All You Need"
    assert m["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert m["year"] == 2017
    assert "Transformer" in m["abstract"]


def test_sources_swallow_network_error_return_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise OSError("no network")

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_doi_metadata("10.0/x") is None
    assert sources.fetch_arxiv_metadata("1706.03762") is None
    assert sources.fetch_pmid_metadata("123") is None


def test_author_year_uses_structured_metadata_full_partial_none():
    cite = Citation(id=1, type="DOI", ref="10.0/x", context="Vaswani et al. (2017).", line=1)
    # full: author + year both align with structured metadata
    full = score_citation(
        cite,
        Fetched(id=1, status=200, authors=["Ashish Vaswani"], year=2017, source="crossref"),
        [Judgement("a", Verdict.MATCH, "r")],
    )
    assert full.breakdown["author_year"] == 20
    # partial: year matches, author does not
    partial = score_citation(
        cite,
        Fetched(id=1, status=200, authors=["Someone Else"], year=2017, source="crossref"),
        [Judgement("a", Verdict.MATCH, "r")],
    )
    assert partial.breakdown["author_year"] == 10
    # none: neither matches
    none = score_citation(
        cite,
        Fetched(id=1, status=200, authors=["Someone Else"], year=1999, source="crossref"),
        [Judgement("a", Verdict.MATCH, "r")],
    )
    assert none.breakdown["author_year"] == 0


# ---------------------------------------------------------------------------
# Feature 2 — soft-404 detection + L1 downgrade
# ---------------------------------------------------------------------------


def test_detect_soft_404_markers():
    assert _detect_soft_404("404 Not Found", "x" * 400, "http://a/deep", "http://a/deep")
    assert _detect_soft_404("Home", "찾을 수 없는 페이지 " + "x" * 400, "http://a/deep", "http://a/deep")


def test_detect_soft_404_redirect_to_root():
    assert _detect_soft_404("Home", "y" * 400, "http://a/deep/path", "http://a/")


def test_detect_soft_404_tiny_body():
    assert _detect_soft_404("OK", "short", "http://a/deep", "http://a/deep")


def test_detect_soft_404_genuine_page_not_flagged():
    body = "This page genuinely discusses the cited topic in detail. " * 10
    assert not _detect_soft_404("Real Article Title", body, "http://a/deep", "http://a/deep")


def test_l1_soft_404_downgrades_100_to_50():
    cite = Citation(id=1, type="URL", ref="https://x.com/page", context="c", line=1)
    suspect = score_citation(
        cite, Fetched(id=1, status=200, soft_404_suspect=True), [], level="L1"
    )
    assert suspect.score == 50.0
    assert suspect.breakdown == {"url_alive": 50}

    clean = score_citation(
        cite, Fetched(id=1, status=200, soft_404_suspect=False), [], level="L1"
    )
    assert clean.score == 100.0
    assert clean.breakdown == {"url_alive": 100}


# ---------------------------------------------------------------------------
# Feature 3 — Uncertain scoring + tie-break consensus
# ---------------------------------------------------------------------------


def _cite():
    return Citation(id=1, type="URL", ref="https://x.com", context="Smith 2017 gain.", line=1)


def _fetched():
    return Fetched(id=1, status=200, title="Smith 2017", abstract="In 2017 Smith reported a gain.")


def _j(name, verdict):
    return Judgement(name, verdict, "r")


def test_uncertain_claim_match_is_15():
    sc = score_citation(_cite(), _fetched(), [_j("a", Verdict.UNCERTAIN)])
    assert sc.breakdown["claim_match"] == 15  # below Partial=25, above Inaccessible=10


def test_disagree_with_tiebreak_resolves_consensus():
    # Two judges disagree; tie-break supplied -> consensus reached.
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.PARTIAL)],
        tiebreak_judgement=_j("t", Verdict.PARTIAL),
    )
    # majority across {Match, Partial, Partial} == Partial == 25
    assert sc.breakdown["claim_match"] == 25
    # M1 fix: only ONE *primary* judge (b) holds the winning verdict Partial; the
    # tie-break arbiter is not a corroborating peer, so this is not a
    # cross-checked consensus. The prior cross_check==10 fixated the M1 defect.
    assert sc.breakdown["cross_check"] == 0


def test_two_primary_agreeing_with_tiebreak_keeps_cross_check():
    # Two primary judges already agree on Partial (+ a dissenter); the winner has
    # >=2 substantive primary supporters -> genuine cross-check, 10 pts.
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.PARTIAL), _j("b", Verdict.PARTIAL), _j("c", Verdict.MATCH)],
        tiebreak_judgement=_j("t", Verdict.PARTIAL),
    )
    assert sc.breakdown["claim_match"] == 25
    assert sc.breakdown["cross_check"] == 10


def test_disagree_no_tiebreak_is_uncertain_and_zero_cross_check():
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.MISMATCH)],
    )
    assert sc.breakdown["cross_check"] == 0
    assert sc.breakdown["claim_match"] == 15  # effective verdict downgraded to Uncertain


def test_agree_still_gets_cross_check_unchanged():
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.MATCH), _j("b", Verdict.MATCH)],
    )
    assert sc.breakdown["cross_check"] == 10
    assert sc.breakdown["claim_match"] == 50


def test_parse_response_recognises_uncertain():
    from paperverify.judge import parse_response

    v, reason = parse_response("VERDICT: Uncertain\nREASON: source does not settle it")
    assert v is Verdict.UNCERTAIN
    assert "settle" in reason


def test_schema_version_is_5():
    from paperverify.report import SCHEMA_VERSION

    # Bumped 4 -> 5 (additive): top-level "keyword_only" flag (H2 report signal).
    assert SCHEMA_VERSION == "5"


def test_fetched_round_trips_new_fields():
    f = Fetched(
        id=1, status=200, authors=["A B"], year=2020, source="crossref", soft_404_suspect=True
    )
    d = f.to_dict()
    assert d["authors"] == ["A B"]
    assert d["year"] == 2020
    assert d["source"] == "crossref"
    assert d["soft_404_suspect"] is True
    assert Fetched.from_dict(d) == f


def test_single_judge_behaviour_unchanged_by_tiebreak():
    # A lone tie-break is irrelevant with a single primary judge.
    sc = score_citation(
        _cite(),
        _fetched(),
        [_j("a", Verdict.MATCH)],
        tiebreak_judgement=_j("t", Verdict.MISMATCH),
    )
    assert sc.breakdown["cross_check"] == 0
    assert sc.breakdown["claim_match"] == 50  # primary verdict drives it
