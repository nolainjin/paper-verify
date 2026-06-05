"""Characterization / behavior coverage for ``paperverify.fetch``.

All tests are network-free: ``urllib`` openers, ``_open``, ``_metadata_for`` and
the ``sources.*`` metadata calls are monkeypatched, and ``time.sleep`` is
neutralized so retry/backoff paths are deterministic. These exercise the branches
the existing suite left uncovered: URL resolution per id type, metadata routing
from a raw URL, per-host rate limiting, HTML text extraction, the ``_open``
success / redirect / 303-method-rewrite / too-many-redirects paths, charset
header parsing, ``_fetch_one`` body handling, the ``fetch`` archive-success and
metadata-URLError branches, and ``fetch_all`` parallelism / error isolation.

Intentionally NOT covered (entrypoint / live-only): none in this module — every
branch here is reachable with mocked I/O.
"""

import urllib.error

import pytest

import paperverify.fetch as fetch_mod
from paperverify import sources
from paperverify.models import Citation, Fetched


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Resp:
    """Minimal context-manager HTTP response for a fake opener."""

    def __init__(self, status, url, ctype, body):
        self.status = status
        self._url = url
        self.headers = {"Content-Type": ctype}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self._url

    def read(self, amt=None):
        return self._body if amt is None else self._body[:amt]


def _cite(type_, ref, id_=1):
    return Citation(id=id_, type=type_, ref=ref, context="ctx", line=1)


# ---------------------------------------------------------------------------
# _resolve_url — every id type maps to the right fetchable URL
# ---------------------------------------------------------------------------


def test_resolve_url_plain_url_passthrough():
    assert fetch_mod._resolve_url(_cite("URL", "https://ex.com/p")) == "https://ex.com/p"


def test_resolve_url_doi():
    assert fetch_mod._resolve_url(_cite("DOI", "10.1/x")) == "https://doi.org/10.1/x"


def test_resolve_url_pmc():
    url = fetch_mod._resolve_url(_cite("PMC", "PMC123"))
    assert url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/"


def test_resolve_url_pmid_strips_non_digits():
    url = fetch_mod._resolve_url(_cite("PMID", "PMID: 98765"))
    assert url == "https://pubmed.ncbi.nlm.nih.gov/98765/"


def test_resolve_url_arxiv_strips_prefix():
    url = fetch_mod._resolve_url(_cite("arXiv", "arXiv:2401.01234"))
    assert url == "https://arxiv.org/abs/2401.01234"


def test_resolve_url_unknown_type_returns_ref():
    assert fetch_mod._resolve_url(_cite("WEIRD", "raw-ref")) == "raw-ref"


# ---------------------------------------------------------------------------
# _metadata_for — routing by explicit id type and by id embedded in a URL
# ---------------------------------------------------------------------------


def test_metadata_for_doi_routes_to_crossref(monkeypatch):
    monkeypatch.setattr(sources, "fetch_doi_metadata", lambda ref: {"title": "T"})
    meta, label = fetch_mod._metadata_for(_cite("DOI", "10.1/x"))
    assert meta == {"title": "T"} and label == "crossref"


def test_metadata_for_arxiv_routes_to_arxiv(monkeypatch):
    monkeypatch.setattr(sources, "fetch_arxiv_metadata", lambda ref: {"title": "A"})
    meta, label = fetch_mod._metadata_for(_cite("arXiv", "2401.01234"))
    assert meta == {"title": "A"} and label == "arxiv"


def test_metadata_for_pmid_routes_to_ncbi(monkeypatch):
    monkeypatch.setattr(sources, "fetch_pmid_metadata", lambda ref: {"title": "P"})
    meta, label = fetch_mod._metadata_for(_cite("PMID", "123"))
    assert meta == {"title": "P"} and label == "ncbi"


def test_metadata_for_pmc_routes_to_ncbi(monkeypatch):
    monkeypatch.setattr(sources, "fetch_pmc_metadata", lambda ref: {"title": "C"})
    meta, label = fetch_mod._metadata_for(_cite("PMC", "PMC9"))
    assert meta == {"title": "C"} and label == "ncbi"


def test_metadata_for_url_with_embedded_arxiv(monkeypatch):
    monkeypatch.setattr(sources, "fetch_arxiv_metadata", lambda ref: {"id": ref})
    meta, label = fetch_mod._metadata_for(_cite("URL", "https://arxiv.org/abs/2401.05678"))
    assert label == "arxiv" and meta == {"id": "2401.05678"}


def test_metadata_for_url_with_embedded_pubmed(monkeypatch):
    monkeypatch.setattr(sources, "fetch_pmid_metadata", lambda ref: {"id": ref})
    meta, label = fetch_mod._metadata_for(
        _cite("URL", "https://pubmed.ncbi.nlm.nih.gov/33445566/")
    )
    assert label == "ncbi" and meta == {"id": "33445566"}


def test_metadata_for_url_with_embedded_pmc(monkeypatch):
    monkeypatch.setattr(sources, "fetch_pmc_metadata", lambda ref: {"id": ref})
    meta, label = fetch_mod._metadata_for(
        _cite("URL", "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7654321/")
    )
    assert label == "ncbi" and meta == {"id": "PMC7654321"}


def test_metadata_for_url_with_embedded_doi(monkeypatch):
    monkeypatch.setattr(sources, "fetch_doi_metadata", lambda ref: {"doi": ref})
    meta, label = fetch_mod._metadata_for(
        _cite("URL", "https://example.com/doi/10.1234/abcd.efgh")
    )
    assert label == "crossref" and meta == {"doi": "10.1234/abcd.efgh"}


def test_metadata_for_plain_url_no_match_returns_none():
    meta, label = fetch_mod._metadata_for(_cite("URL", "https://example.com/blog/post"))
    assert meta is None and label == ""


# ---------------------------------------------------------------------------
# _rate_limit — blocks until the per-host interval has elapsed, then proceeds
# ---------------------------------------------------------------------------


def test_rate_limit_sleeps_when_host_called_too_soon(monkeypatch):
    fetch_mod._host_last.clear()
    times = iter([100.0, 100.0, 100.0 + fetch_mod.MIN_HOST_INTERVAL])
    monkeypatch.setattr(fetch_mod.time, "monotonic", lambda: next(times))
    slept = []
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: slept.append(s))
    # Prime the host so the next call sees a recent timestamp and must wait.
    fetch_mod._host_last["ex.com"] = 100.0
    fetch_mod._rate_limit("https://ex.com/p")
    assert slept and slept[0] > 0


def test_rate_limit_no_wait_for_fresh_host(monkeypatch):
    fetch_mod._host_last.clear()
    monkeypatch.setattr(fetch_mod.time, "monotonic", lambda: 500.0)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: pytest.fail("should not sleep"))
    fetch_mod._rate_limit("https://newhost.com/p")
    assert fetch_mod._host_last["newhost.com"] == 500.0


# ---------------------------------------------------------------------------
# _TextExtractor / _strip_html — title, meta description, visible text, skips
# ---------------------------------------------------------------------------


def test_strip_html_extracts_title_meta_and_visible_text():
    html = (
        "<html><head><title>My Title</title>"
        '<meta name="description" content="A short abstract.">'
        "<style>.x{color:red}</style><script>var a=1;</script>"
        "</head><body><p>Visible body text.</p>"
        "<noscript>hidden</noscript></body></html>"
    )
    title, meta, text = fetch_mod._strip_html(html)
    assert title == "My Title"
    assert meta == "A short abstract."
    assert "Visible body text." in text
    # script / style / noscript content is skipped.
    assert "var a=1" not in text
    assert "color:red" not in text
    assert "hidden" not in text


def test_strip_html_meta_og_description_property():
    html = '<meta property="og:description" content="OG abstract">'
    _title, meta, _text = fetch_mod._strip_html(html)
    assert meta == "OG abstract"


def test_strip_html_first_meta_desc_wins():
    html = (
        '<meta name="description" content="first">'
        '<meta name="citation_abstract" content="second">'
    )
    _title, meta, _text = fetch_mod._strip_html(html)
    assert meta == "first"


def test_strip_html_caps_body_at_max_body(monkeypatch):
    monkeypatch.setattr(fetch_mod, "MAX_BODY", 50)
    html = "<body>" + "<p>" + ("word " * 100) + "</p></body>"
    _title, _meta, text = fetch_mod._strip_html(html)
    assert len(text) <= 50


def test_strip_html_swallows_parser_error(monkeypatch):
    class Boom(fetch_mod._TextExtractor):
        def feed(self, data):
            raise RuntimeError("malformed")

    monkeypatch.setattr(fetch_mod, "_TextExtractor", Boom)
    # _strip_html must not raise even when the parser explodes.
    title, meta, text = fetch_mod._strip_html("<html>broken")
    assert (title, meta, text) == ("", "", "")


def test_text_extractor_stops_collecting_past_max_body(monkeypatch):
    # Once the accumulated size hits MAX_BODY, further data chunks are dropped.
    monkeypatch.setattr(fetch_mod, "MAX_BODY", 10)
    p = fetch_mod._TextExtractor()
    p.feed("<body><p>aaaaaaaaaa</p><p>SHOULD_BE_DROPPED</p></body>")
    assert "SHOULD_BE_DROPPED" not in p.text()


# ---------------------------------------------------------------------------
# _detect_soft_404 — redirect-to-root, tiny body, and malformed-URL guard
# ---------------------------------------------------------------------------


def test_detect_soft_404_redirect_deep_to_root():
    # A deep path that ended up at a bare homepage root is a soft-404 signal.
    body = "x" * 400  # long enough to bypass the tiny-body check
    assert fetch_mod._detect_soft_404(
        "Home", body, "https://j.org/articles/deep/path", "https://j.org/"
    )


def test_detect_soft_404_tiny_body():
    assert fetch_mod._detect_soft_404(
        "OK", "short", "https://j.org/x", "https://j.org/x"
    )


def test_detect_soft_404_handles_unsplittable_url():
    # A URL that urlsplit cannot parse must not crash; the tiny-body branch
    # still decides. (Exercises the ValueError guard around urlsplit.)
    long_body = "This is a long, content-rich page body. " * 20
    bad = "http://[::1"  # malformed -> urlsplit raises ValueError
    # No error markers, long body, malformed URL => not flagged, no exception.
    assert fetch_mod._detect_soft_404("Title", long_body, bad, bad) is False


# ---------------------------------------------------------------------------
# _NoRedirect — redirect_request returns None so the outer loop handles it
# ---------------------------------------------------------------------------


def test_no_redirect_handler_returns_none():
    handler = fetch_mod._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "http://x/") is None


# ---------------------------------------------------------------------------
# _guard_url — missing host and unresolvable host
# ---------------------------------------------------------------------------


def test_guard_url_missing_host_raises():
    with pytest.raises(ValueError, match="missing host"):
        fetch_mod._guard_url("https:///path-only")


def test_guard_url_unresolvable_host_raises(monkeypatch):
    def boom(host, port, **kw):
        import socket as _socket

        raise _socket.gaierror("name resolution failed")

    monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="unresolvable host"):
        fetch_mod._guard_url("https://nonexistent.invalid/p")


def test_guard_url_allows_public_address(monkeypatch):
    # A public IP passes the guard (no exception).
    monkeypatch.setattr(
        fetch_mod.socket, "getaddrinfo",
        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    fetch_mod._guard_url("https://example.com/p")  # must not raise


# ---------------------------------------------------------------------------
# _archive_url — malformed availability JSON degrades to the redirect form
# ---------------------------------------------------------------------------


def test_archive_url_bad_json_falls_back_to_redirect_form(monkeypatch):
    # 2xx from the availability API but a non-JSON body -> redirect-form fallback.
    monkeypatch.setattr(
        fetch_mod, "_open",
        lambda url, method: (200, url, "text/html", b"<html>not json</html>"),
    )
    resolved = fetch_mod._archive_url("https://ex.com/p")
    assert resolved == "https://web.archive.org/web/2/https://ex.com/p"


# ---------------------------------------------------------------------------
# _decode_body — both candidates unknown -> final utf-8 fallback return
# ---------------------------------------------------------------------------


def test_decode_body_no_charset_no_meta_uses_utf8():
    # No header charset, no <meta charset> -> the charset candidate is None and
    # the loop's "utf-8" candidate decodes the body.
    text = fetch_mod._decode_body("plain text body".encode("utf-8"), "text/html")
    assert text == "plain text body"


# ---------------------------------------------------------------------------
# _open — success path, redirect following, 303 method rewrite, redirect cap
# ---------------------------------------------------------------------------


def _patch_guard_ok(monkeypatch):
    """Disable SSRF/DNS resolution so _open can run against fake openers."""
    monkeypatch.setattr(fetch_mod, "_guard_url", lambda url: None)


def test_open_success_returns_status_final_ctype_body(monkeypatch):
    _patch_guard_ok(monkeypatch)

    class OK:
        def open(self, req, timeout=None):
            return _Resp(200, req.full_url, "text/html; charset=utf-8", b"<html>ok</html>")

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: OK())
    status, final, ctype, body = fetch_mod._open("https://ex.com/p", "GET")
    assert status == 200
    assert final == "https://ex.com/p"
    assert "text/html" in ctype
    assert body == b"<html>ok</html>"


def test_open_head_method_discards_body(monkeypatch):
    _patch_guard_ok(monkeypatch)

    class OK:
        def open(self, req, timeout=None):
            return _Resp(200, req.full_url, "text/html", b"should-not-be-read")

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: OK())
    status, _final, _ctype, body = fetch_mod._open("https://ex.com/p", "HEAD")
    assert status == 200
    assert body == b""  # HEAD never reads a body


def test_open_follows_redirect_to_final(monkeypatch):
    _patch_guard_ok(monkeypatch)
    seen = []

    class Redirecting:
        def open(self, req, timeout=None):
            seen.append(req.full_url)
            if req.full_url == "https://ex.com/start":
                raise urllib.error.HTTPError(
                    req.full_url, 302, "Found",
                    {"Location": "https://ex.com/final"}, None,
                )
            return _Resp(200, req.full_url, "text/html", b"final body")

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: Redirecting())
    status, final, _ctype, body = fetch_mod._open("https://ex.com/start", "GET")
    assert status == 200
    assert final == "https://ex.com/final"
    assert body == b"final body"
    assert seen == ["https://ex.com/start", "https://ex.com/final"]


def test_open_303_rewrites_method_to_get(monkeypatch):
    _patch_guard_ok(monkeypatch)
    methods = []

    class Redirecting:
        def open(self, req, timeout=None):
            methods.append(req.get_method())
            if req.full_url == "https://ex.com/post":
                raise urllib.error.HTTPError(
                    req.full_url, 303, "See Other",
                    {"Location": "https://ex.com/result"}, None,
                )
            return _Resp(200, req.full_url, "text/html", b"x")

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: Redirecting())
    fetch_mod._open("https://ex.com/post", "POST")
    # First hop POST, second hop rewritten to GET after the 303.
    assert methods == ["POST", "GET"]


def test_open_redirect_without_location_reraises(monkeypatch):
    _patch_guard_ok(monkeypatch)

    class Redirecting:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {}, None)

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: Redirecting())
    with pytest.raises(urllib.error.HTTPError):
        fetch_mod._open("https://ex.com/p", "GET")


def test_open_too_many_redirects_raises(monkeypatch):
    _patch_guard_ok(monkeypatch)

    class AlwaysRedirect:
        def __init__(self):
            self.n = 0

        def open(self, req, timeout=None):
            self.n += 1
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found",
                {"Location": f"https://ex.com/r{self.n}"}, None,
            )

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: AlwaysRedirect())
    with pytest.raises(ValueError, match="too many redirects"):
        fetch_mod._open("https://ex.com/start", "GET")


def test_open_retries_once_on_urlerror_then_raises(monkeypatch):
    _patch_guard_ok(monkeypatch)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Flaky:
        def open(self, req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("conn refused")

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *h: Flaky())
    with pytest.raises(urllib.error.URLError):
        fetch_mod._open("https://ex.com/p", "GET")
    assert calls["n"] == 2  # initial try + one retry, then propagate


# ---------------------------------------------------------------------------
# _charset_from_header — present, absent, quoted
# ---------------------------------------------------------------------------


def test_charset_from_header_present():
    assert fetch_mod._charset_from_header("text/html; charset=utf-8") == "utf-8"


def test_charset_from_header_absent_returns_none():
    assert fetch_mod._charset_from_header("text/html") is None


def test_charset_from_header_empty_returns_none():
    assert fetch_mod._charset_from_header("") is None


def test_charset_from_header_strips_quotes():
    assert fetch_mod._charset_from_header('text/html; charset="EUC-KR"') == "EUC-KR"


# ---------------------------------------------------------------------------
# _decode_body — unknown codec name falls through to utf-8
# ---------------------------------------------------------------------------


def test_decode_body_unknown_codec_falls_back_to_utf8():
    # A bogus declared charset must not raise; it falls through to utf-8.
    text = fetch_mod._decode_body("héllo wörld".encode("utf-8"), "text/html; charset=bogus-codec-xyz")
    assert "llo" in text  # decoded as utf-8, no exception


# ---------------------------------------------------------------------------
# _fetch_one — L1 status only vs L2 text extraction + soft-404 flagging
# ---------------------------------------------------------------------------


def test_fetch_one_l1_status_only(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_rate_limit", lambda url: None)
    monkeypatch.setattr(
        fetch_mod, "_open",
        lambda url, method: (200, url, "text/html", b"<html>body</html>"),
    )
    f = fetch_mod._fetch_one("https://ex.com/p", "L1")
    assert f.status == 200
    assert f.source == "http"
    assert f.title == ""  # L1 never strips text
    assert f.abstract == ""


def test_fetch_one_l2_extracts_title_and_abstract(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_rate_limit", lambda url: None)
    body = (
        "<html><head><title>Paper Title</title>"
        '<meta name="description" content="The abstract here.">'
        "</head><body>"
        + "<p>" + ("Body sentence with enough length. " * 10) + "</p>"
        + "</body></html>"
    )
    monkeypatch.setattr(
        fetch_mod, "_open",
        lambda url, method: (200, url, "text/html", body.encode("utf-8")),
    )
    f = fetch_mod._fetch_one("https://ex.com/p", "L2")
    assert f.title == "Paper Title"
    assert "The abstract here." in f.abstract
    assert f.ok is True
    assert f.soft_404_suspect is False  # healthy long page


def test_fetch_one_l2_flags_soft_404(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_rate_limit", lambda url: None)
    body = "<html><head><title>Page Not Found</title></head><body>nope</body></html>"
    monkeypatch.setattr(
        fetch_mod, "_open",
        lambda url, method: (200, url, "text/html", body.encode("utf-8")),
    )
    f = fetch_mod._fetch_one("https://ex.com/missing", "L2")
    assert f.ok is True
    assert f.soft_404_suspect is True


def test_fetch_one_no_meta_uses_visible_text_as_abstract(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_rate_limit", lambda url: None)
    body = "<html><body><p>" + ("Only visible prose here. " * 12) + "</p></body></html>"
    monkeypatch.setattr(
        fetch_mod, "_open",
        lambda url, method: (200, url, "text/html", body.encode("utf-8")),
    )
    f = fetch_mod._fetch_one("https://ex.com/p", "L2")
    assert "Only visible prose here." in f.abstract


# ---------------------------------------------------------------------------
# fetch — full fallback chain (metadata / http / archive)
# ---------------------------------------------------------------------------


def test_fetch_metadata_url_error_landing_keeps_url(monkeypatch):
    # Metadata hit, but the landing status probe raises a non-HTTP URLError:
    # final stays the resolved URL and landing_status stays None.
    monkeypatch.setattr(
        fetch_mod, "_metadata_for",
        lambda c: ({"title": "T", "abstract": "A", "authors": ["X"], "year": 2021}, "crossref"),
    )

    def boom(url, method):
        raise urllib.error.URLError("landing down")

    monkeypatch.setattr(fetch_mod, "_open", boom)
    f = fetch_mod.fetch(_cite("DOI", "10.1/x"), "L2")
    assert f.source == "crossref"
    assert f.status == 200
    assert f.landing_status is None
    assert f.url_final == "https://doi.org/10.1/x"
    assert f.year == 2021 and f.authors == ["X"]


def test_fetch_metadata_lookup_exception_falls_through_to_http(monkeypatch):
    # A crashing metadata lookup must not kill the run; it falls through to HTTP.
    def crash(c):
        raise RuntimeError("metadata layer blew up")

    monkeypatch.setattr(fetch_mod, "_metadata_for", crash)

    def fake_fetch_one(url, level):
        return Fetched(id=0, status=200, title="HTTP Title", source="http")

    monkeypatch.setattr(fetch_mod, "_fetch_one", fake_fetch_one)
    f = fetch_mod.fetch(_cite("URL", "https://ex.com/p", id_=7), "L2")
    assert f.source == "http"
    assert f.id == 7
    assert f.title == "HTTP Title"


def test_fetch_http_success_no_metadata(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_metadata_for", lambda c: (None, ""))
    monkeypatch.setattr(
        fetch_mod, "_fetch_one",
        lambda url, level: Fetched(id=0, status=200, source="http", title="ok"),
    )
    f = fetch_mod.fetch(_cite("URL", "https://ex.com/p", id_=9), "L2")
    assert f.source == "http"
    assert f.id == 9


def test_fetch_falls_back_to_archive_snapshot(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_metadata_for", lambda c: (None, ""))
    calls = {"n": 0}

    def fetch_one(url, level):
        calls["n"] += 1
        if calls["n"] == 1:  # direct HTTP fails
            raise urllib.error.HTTPError(url, 500, "err", {}, None)
        # archive snapshot succeeds
        return Fetched(id=0, status=200, title="Archived", source="http")

    monkeypatch.setattr(fetch_mod, "_fetch_one", fetch_one)
    monkeypatch.setattr(
        fetch_mod, "_archive_url",
        lambda url: "http://web.archive.org/web/2020/https://ex.com/p",
    )
    f = fetch_mod.fetch(_cite("URL", "https://ex.com/p", id_=4), "L2")
    assert f.via_archive is True
    assert f.source == "archive"
    assert f.id == 4
    assert f.title == "Archived"


def test_fetch_archive_also_fails_records_both_errors(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_metadata_for", lambda c: (None, ""))

    def fetch_one(url, level):
        raise urllib.error.HTTPError(url, 503, "err", {}, None)

    monkeypatch.setattr(fetch_mod, "_fetch_one", fetch_one)
    monkeypatch.setattr(
        fetch_mod, "_archive_url",
        lambda url: "http://web.archive.org/web/2020/https://ex.com/p",
    )
    f = fetch_mod.fetch(_cite("URL", "https://ex.com/p", id_=5), "L2")
    assert f.source == "none"
    assert f.status == 503
    assert "archive:" in f.error


# ---------------------------------------------------------------------------
# fetch_all — empty, parallel collection, level validation, error isolation
# ---------------------------------------------------------------------------


def test_fetch_all_empty_returns_empty():
    assert fetch_mod.fetch_all([], level="L2") == {}


def test_fetch_all_invalid_level_raises():
    with pytest.raises(ValueError, match="unknown level"):
        fetch_mod.fetch_all([_cite("URL", "https://ex.com/p")], level="L9")


def test_fetch_all_collects_results_by_id(monkeypatch):
    monkeypatch.setattr(
        fetch_mod, "fetch",
        lambda c, level: Fetched(id=c.id, status=200, title=f"t{c.id}"),
    )
    cites = [_cite("URL", "https://ex.com/a", 1), _cite("URL", "https://ex.com/b", 2)]
    out = fetch_mod.fetch_all(cites, level="L2", workers=2)
    assert set(out.keys()) == {1, 2}
    assert out[1].title == "t1" and out[2].title == "t2"


def test_fetch_all_isolates_per_citation_exception(monkeypatch):
    def maybe_boom(c, level):
        if c.id == 1:
            raise RuntimeError("URL 1 exploded")
        return Fetched(id=c.id, status=200)

    monkeypatch.setattr(fetch_mod, "fetch", maybe_boom)
    cites = [_cite("URL", "https://ex.com/a", 1), _cite("URL", "https://ex.com/b", 2)]
    out = fetch_mod.fetch_all(cites, level="L2", workers=2)
    # The crash is captured as an error Fetched; the other citation still resolves.
    assert out[1].error == "URL 1 exploded"
    assert out[2].status == 200
