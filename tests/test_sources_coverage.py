"""Characterization coverage for paperverify.sources.

Targets the uncovered branches the existing test_sources.py /
test_sources_arxiv.py / test_p1_robustness.py leave open: ``_get``'s HTTPError
404 / non-retryable / retryable-then-raise paths, ``_clean_abstract`` empty,
the empty/blank-identifier early returns in every ``fetch_*`` function,
``_crossref_year`` malformed-node branches, arXiv missing-entry / empty-record /
bad-year paths, PMID error-record / no-title paths, and ``fetch_pmc_metadata``
(idconv -> PMID) which was wholly uncovered.

Network-free and deterministic: ``urllib`` and ``time.sleep`` are monkeypatched;
no real HTTP, no real waiting.
"""

import json
import urllib.error

import pytest

from paperverify import sources


# ---------------------------------------------------------------------------
# _clean_abstract — empty short-circuit (line 98-99)
# ---------------------------------------------------------------------------


def test_clean_abstract_empty_returns_empty():
    assert sources._clean_abstract("") == ""


def test_clean_abstract_strips_tags_and_collapses_whitespace():
    out = sources._clean_abstract("<jats:p>We   propose\n the\tTransformer.</jats:p>")
    assert "<" not in out
    assert out == "We propose the Transformer."


# ---------------------------------------------------------------------------
# _get — HTTP error handling (lines 124-135)
# ---------------------------------------------------------------------------


class _OkResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, amt=None):
        return self._payload if amt is None else self._payload[:amt]


def test_get_404_raises_notfound(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    sources._reset_host_clock()
    with pytest.raises(sources._NotFound):
        sources._get("https://api.crossref.org/works/10.0/missing")


def test_get_non_retryable_4xx_reraised_immediately(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources._reset_host_clock()
    with pytest.raises(urllib.error.HTTPError):
        sources._get("https://api.crossref.org/works/10.0/x")
    assert calls["n"] == 1  # 401 is not in _RETRY_STATUSES -> no retry


def test_get_retryable_5xx_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 503, "Busy", {}, None)

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources._reset_host_clock()
    with pytest.raises(urllib.error.HTTPError):
        sources._get("https://api.crossref.org/works/10.0/x")
    assert calls["n"] == 2  # 503 retried once, then raised (last_exc)


def test_get_urlerror_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources._reset_host_clock()
    with pytest.raises(urllib.error.URLError):
        sources._get("https://api.crossref.org/works/10.0/x")
    assert calls["n"] == 2  # transient -> retried once, then raised


def test_get_success_returns_body(monkeypatch):
    monkeypatch.setattr(
        sources.urllib.request, "urlopen",
        lambda req, timeout=None: _OkResp(b"{}"),
    )
    sources._reset_host_clock()
    assert sources._get("https://api.crossref.org/works/10.0/x") == b"{}"


# ---------------------------------------------------------------------------
# fetch_doi_metadata — empty id, non-dict message, missing fields (lines 147,
# 156-157, 165, 176)
# ---------------------------------------------------------------------------


def test_fetch_doi_blank_returns_none():
    assert sources.fetch_doi_metadata("   ") is None


def test_fetch_doi_message_not_dict_returns_none(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": json.dumps({"message": []}).encode())
    assert sources.fetch_doi_metadata("10.0/x") is None


def test_fetch_doi_skips_non_dict_author_and_uses_name_field(monkeypatch):
    payload = json.dumps(
        {
            "message": {
                "title": ["T"],
                "author": [
                    "not-a-dict",  # skipped (line 165 continue)
                    {"name": "Org Author"},  # uses fallback name
                    {"family": "Vaswani", "given": "Ashish"},
                ],
                "issued": {"date-parts": [[2017]]},
            }
        }
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    m = sources.fetch_doi_metadata("10.0/x")
    assert m["authors"] == ["Org Author", "Ashish Vaswani"]
    assert m["year"] == 2017


def test_fetch_doi_title_as_bare_string(monkeypatch):
    payload = json.dumps({"message": {"title": "Bare String Title", "author": []}}).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    m = sources.fetch_doi_metadata("10.0/x")
    assert m["title"] == "Bare String Title"


def test_fetch_doi_empty_record_returns_none(monkeypatch):
    # No title, no authors, no abstract -> None (line 176).
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": json.dumps({"message": {}}).encode())
    assert sources.fetch_doi_metadata("10.0/x") is None


def test_fetch_doi_falls_back_to_published_when_issued_missing(monkeypatch):
    payload = json.dumps(
        {"message": {"title": ["T"], "published": {"date-parts": [[2021, 3]]}}}
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    m = sources.fetch_doi_metadata("10.0/x")
    assert m["year"] == 2021


def test_fetch_doi_notfound_returns_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise sources._NotFound(url)

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_doi_metadata("10.0/x") is None


# ---------------------------------------------------------------------------
# _crossref_year — malformed nodes (lines 183, 188-190)
# ---------------------------------------------------------------------------


def test_crossref_year_non_dict_node():
    assert sources._crossref_year(["not", "a", "dict"]) is None


def test_crossref_year_missing_date_parts():
    assert sources._crossref_year({"foo": "bar"}) is None


def test_crossref_year_non_int_first_part():
    # date-parts[0][0] is not coercible to int -> ValueError caught -> None.
    assert sources._crossref_year({"date-parts": [["notyear"]]}) is None


def test_crossref_year_valid():
    assert sources._crossref_year({"date-parts": [[1998, 5]]}) == 1998


# ---------------------------------------------------------------------------
# fetch_arxiv_metadata — empty id, no entry, empty record, bad year (lines
# 205, 211, 217, 242-243, 246)
# ---------------------------------------------------------------------------


def test_fetch_arxiv_blank_id_returns_none():
    assert sources.fetch_arxiv_metadata("arXiv:") is None
    assert sources.fetch_arxiv_metadata("   ") is None


def test_fetch_arxiv_notfound_returns_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise sources._NotFound(url)

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_arxiv_metadata("1706.03762") is None


def test_fetch_arxiv_no_entry_returns_none(monkeypatch):
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": feed)
    assert sources.fetch_arxiv_metadata("1706.03762") is None


def test_fetch_arxiv_bad_published_year_is_none_but_record_kept(monkeypatch):
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<title>Real Paper</title>"
        "<published>notayear-string-here</published>"
        "</entry></feed>"
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": feed)
    m = sources.fetch_arxiv_metadata("1706.03762")
    assert m["title"] == "Real Paper"
    assert m["year"] is None


def test_fetch_arxiv_empty_record_returns_none(monkeypatch):
    # entry present but no title/authors/abstract -> None (line 246).
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<title></title>"
        "</entry></feed>"
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": feed)
    assert sources.fetch_arxiv_metadata("1706.03762") is None


def test_fetch_arxiv_strips_namespace_prefix(monkeypatch):
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<title>T</title><published>2019-01-01T00:00:00Z</published>"
        "</entry></feed>"
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": feed)
    # "arxiv:" prefix is split off (line 202-203).
    m = sources.fetch_arxiv_metadata("arxiv:1901.00001")
    assert m["year"] == 2019


# ---------------------------------------------------------------------------
# fetch_pmid_metadata — blank, error record, no title (lines 259, 278, 296)
# ---------------------------------------------------------------------------


def test_fetch_pmid_blank_returns_none():
    assert sources.fetch_pmid_metadata("no-digits-here") is None


def test_fetch_pmid_error_record_returns_none(monkeypatch):
    payload = json.dumps(
        {"result": {"123": {"uid": "123", "error": "cannot get document summary"}}}
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    assert sources.fetch_pmid_metadata("123") is None


def test_fetch_pmid_missing_record_returns_none(monkeypatch):
    # result has no entry for the digits key -> rec is None (line 277-278).
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": json.dumps({"result": {}}).encode())
    assert sources.fetch_pmid_metadata("123") is None


def test_fetch_pmid_no_title_no_authors_returns_none(monkeypatch):
    payload = json.dumps(
        {"result": {"123": {"uid": "123", "title": "", "authors": []}}}
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    assert sources.fetch_pmid_metadata("123") is None


def test_fetch_pmid_no_year_when_pubdate_absent(monkeypatch):
    payload = json.dumps(
        {"result": {"123": {"uid": "123", "title": "Has Title", "authors": []}}}
    ).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": payload)
    m = sources.fetch_pmid_metadata("123")
    assert m["title"] == "Has Title"
    assert m["year"] is None


def test_fetch_pmid_notfound_returns_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise sources._NotFound(url)

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_pmid_metadata("123") is None


# ---------------------------------------------------------------------------
# fetch_pmc_metadata — wholly uncovered (lines 302-329)
# ---------------------------------------------------------------------------


def test_fetch_pmc_blank_returns_none():
    # "PMC" with no digits -> ident == "PMC" -> None (line 305-306).
    assert sources.fetch_pmc_metadata("PMC") is None
    assert sources.fetch_pmc_metadata("") is None


def test_fetch_pmc_resolves_pmid_then_delegates(monkeypatch):
    idconv = json.dumps({"records": [{"pmcid": "PMC555", "pmid": "999"}]}).encode()
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": idconv)
    # Delegated PMID lookup is stubbed so this stays a pure pmc-path test.
    monkeypatch.setattr(
        sources, "fetch_pmid_metadata",
        lambda pmid: {"title": "Delegated", "authors": ["X"], "year": 2020, "abstract": ""} if pmid == "999" else None,
    )
    m = sources.fetch_pmc_metadata("PMC555")
    assert m["title"] == "Delegated"


def test_fetch_pmc_normalizes_bare_digits(monkeypatch):
    captured = {}

    def fake_get(url, accept="*/*"):
        captured["url"] = url
        return json.dumps({"records": [{"pmid": "12"}]}).encode()

    monkeypatch.setattr(sources, "_get", fake_get)
    monkeypatch.setattr(sources, "fetch_pmid_metadata", lambda pmid: {"ok": pmid})
    out = sources.fetch_pmc_metadata("555123")  # no PMC prefix -> normalized
    assert "PMC555123" in captured["url"]
    assert out == {"ok": "12"}


def test_fetch_pmc_no_pmid_in_records_returns_none(monkeypatch):
    idconv = json.dumps({"records": [{"pmcid": "PMC555"}]}).encode()  # no pmid key
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": idconv)
    assert sources.fetch_pmc_metadata("PMC555") is None


def test_fetch_pmc_empty_records_returns_none(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda url, accept="*/*": json.dumps({"records": []}).encode())
    assert sources.fetch_pmc_metadata("PMC555") is None


def test_fetch_pmc_notfound_returns_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise sources._NotFound(url)

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_pmc_metadata("PMC555") is None


def test_fetch_pmc_network_error_returns_none(monkeypatch):
    def boom(url, accept="*/*"):
        raise OSError("no network")

    monkeypatch.setattr(sources, "_get", boom)
    assert sources.fetch_pmc_metadata("PMC555") is None
