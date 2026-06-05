"""Source fetching for citations — stdlib only (``urllib.request``).

No third-party HTTP dependency. Parallelism uses
``concurrent.futures.ThreadPoolExecutor`` (this replaces the original
external multi-CLI parallel fetch). A small ``HTMLParser`` strips markup to
text for L2/L3. On failure the fetcher retries via the Wayback Machine.

Levels:
    L1  HTTP status only (HEAD-like GET, body discarded).
    L2  fetch body, strip to text, populate title + abstract (capped ~50KB).
    L3  same as L2 (callers may pass more of the body to the judge).
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

from . import sources
from .models import Citation, Fetched

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 paper-verify/0.1"
)
TIMEOUT = 10  # seconds
MAX_BODY = 50 * 1024  # ~50KB of stripped text kept
MIN_HOST_INTERVAL = 1.0  # seconds between calls to the same host
MAX_REDIRECTS = 5
# Transient HTTP statuses worth one retry on the main fetch path (mirrors
# sources._get). A single transient blip (rate limit / 5xx / timeout) otherwise
# dropped the citation straight to the Wayback fallback (audit P1-1 / FR-02).
_HTTP_RETRY_STATUSES = {429, 500, 502, 503, 504}
_HTTP_RETRY_BACKOFF = 0.5  # seconds before the single retry

# Per-host rate limiting shared across worker threads.
_host_lock = threading.Lock()
_host_last: dict[str, float] = {}

# Specific phrases that signal a 2xx response is actually a soft-404 / error /
# placeholder page rather than the cited content. Deliberately narrow multi-word
# phrases: bare "error" / "404" appear in legitimate academic prose ("standard
# error", "type I error", "404 nm") and must NOT trip the heuristic (audit P1-3 /
# CL-6 / FR-07). Heuristic — not perfect (see README caveat).
_SOFT_404_MARKERS = (
    "page not found",
    "404 not found",
    "404 error",
    "error 404",
    "not found error",
    "page does not exist",
    "page not exist",
    "page no longer exists",
    "page no longer available",
    "page you requested could not be found",
    "page cannot be found",
    "could not be found",
    "no longer available",
    "content not available",
    "찾을 수 없",
    "존재하지 않",
    "페이지를 찾을 수",
)
_MIN_BODY_CHARS = 200  # stripped text shorter than this on a 2xx is suspicious
# DOI / arXiv / PubMed id patterns inside a raw URL (for metadata routing).
_DOI_IN_URL = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
_ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.I)
_PUBMED_IN_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.I)
_PMC_IN_URL = re.compile(r"(PMC\d+)", re.I)


def _detect_soft_404(title: str, text: str, original_url: str, final_url: str) -> bool:
    """Heuristically flag a reachable (2xx) page that looks like an error stub.

    Triggers when: error markers appear in the title or first ~500 chars; OR a
    deep-path request redirected to a bare site root; OR the stripped body text
    is suspiciously tiny.
    """
    head = f"{title} {text[:500]}".lower()
    if any(marker in head for marker in _SOFT_404_MARKERS):
        return True
    # Redirected from a deep path to a bare homepage/root.
    try:
        orig = urllib.parse.urlsplit(original_url)
        fin = urllib.parse.urlsplit(final_url) if final_url else orig
    except ValueError:
        orig = fin = None
    if orig is not None and fin is not None:
        orig_deep = len(orig.path.strip("/")) > 0
        fin_root = fin.path.strip("/") == "" and not fin.query
        if orig_deep and fin_root and orig.netloc and fin.netloc:
            return True
    # Suspiciously tiny body after stripping.
    if len(text.strip()) < _MIN_BODY_CHARS:
        return True
    return False


def _metadata_for(citation: Citation) -> tuple[dict | None, str]:
    """Try the structured metadata API matching this citation's id/type.

    Returns ``(metadata_dict, source_label)``. ``metadata_dict`` is ``None`` when
    no API applies or the lookup failed; ``source_label`` is the path that *would*
    be recorded on success ("crossref" | "arxiv" | "ncbi" | "").
    """
    t = citation.type
    ref = citation.ref

    if t == "DOI":
        return sources.fetch_doi_metadata(ref), "crossref"
    if t == "arXiv":
        return sources.fetch_arxiv_metadata(ref), "arxiv"
    if t == "PMID":
        return sources.fetch_pmid_metadata(ref), "ncbi"
    if t == "PMC":
        return sources.fetch_pmc_metadata(ref), "ncbi"

    if t == "URL":
        m = _ARXIV_IN_URL.search(ref)
        if m:
            return sources.fetch_arxiv_metadata(m.group(1)), "arxiv"
        m = _PUBMED_IN_URL.search(ref)
        if m:
            return sources.fetch_pmid_metadata(m.group(1)), "ncbi"
        m = _PMC_IN_URL.search(ref)
        if m and "ncbi.nlm.nih.gov" in ref.lower():
            return sources.fetch_pmc_metadata(m.group(1)), "ncbi"
        m = _DOI_IN_URL.search(ref)
        if m:
            return sources.fetch_doi_metadata(m.group(0)), "crossref"
    return None, ""


def _resolve_url(citation: Citation) -> str:
    """Map a citation to a fetchable URL based on its type."""
    ref = citation.ref
    t = citation.type
    if t == "URL":
        return ref
    if t == "DOI":
        return f"https://doi.org/{ref}"
    if t == "PMC":
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{ref}/"
    if t == "PMID":
        digits = "".join(c for c in ref if c.isdigit())
        return f"https://pubmed.ncbi.nlm.nih.gov/{digits}/"
    if t == "arXiv":
        ident = ref.split(":", 1)[-1].strip()
        return f"https://arxiv.org/abs/{ident}"
    return ref


def _rate_limit(url: str) -> None:
    """Block until at least ``MIN_HOST_INTERVAL`` has passed for this host."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    while True:
        with _host_lock:
            now = time.monotonic()
            last = _host_last.get(host, 0.0)
            wait = MIN_HOST_INTERVAL - (now - last)
            if wait <= 0:
                _host_last[host] = now
                return
        time.sleep(wait)


class _TextExtractor(HTMLParser):
    """Collect visible text, the <title>, and any meta description/abstract."""

    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_desc = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._size = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description", "citation_abstract", "dc.description"):
                if a.get("content") and not self.meta_desc:
                    self.meta_desc = a["content"].strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        text = data.strip()
        if not text or self._size >= MAX_BODY:
            return
        self._chunks.append(text)
        self._size += len(text)

    def text(self) -> str:
        return " ".join(self._chunks)[:MAX_BODY]


def _strip_html(body: str) -> tuple[str, str, str]:
    """Return (title, meta_description, visible_text) from an HTML body."""
    parser = _TextExtractor()
    try:
        parser.feed(body)
    except Exception:  # malformed HTML — keep whatever was parsed
        pass
    return parser.title.strip(), parser.meta_desc, parser.text()


_ALLOWED_SCHEMES = {"http", "https"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _guard_url(url: str) -> None:
    """Reject SSRF-prone targets before any request is issued.

    Blocks non-http(s) schemes (file://, ftp://, gopher://, ...) and hosts that
    resolve to private / loopback / link-local / reserved addresses (e.g. cloud
    metadata at 169.254.169.254, 127.0.0.1, 10.0.0.0/8). Critical when paper-
    verify runs as an MCP server / shared service with untrusted document input.
    Raises ValueError on a blocked target. (TOCTOU between resolve and connect
    is not closed here; a re-checking opener would be the rigorous version.)
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"blocked URL scheme: {parts.scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError("blocked URL: missing host")
    try:
        infos = socket.getaddrinfo(host, parts.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"unresolvable host: {host}") from exc
    for *_rest, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"blocked internal address: {ip} (host {host!r})")


def _open(url: str, method: str) -> tuple[int, str, str, bytes]:
    """Open ``url`` following redirects. Returns (status, final_url, ctype, body)."""
    current = url
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(MAX_REDIRECTS + 1):
        _guard_url(current)
        req = urllib.request.Request(
            current, method=method, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
        )
        # Issue the request with one transient-error retry (429 / 5xx / timeout)
        # before giving up this hop. A redirect HTTPError is *not* an error here:
        # it carries the Location and is resolved by the outer loop.
        redirect_exc: urllib.error.HTTPError | None = None
        for attempt in range(2):
            try:
                with opener.open(req, timeout=TIMEOUT) as resp:
                    status = resp.status
                    final = resp.geturl()
                    ctype = resp.headers.get("Content-Type", "")
                    body = resp.read(MAX_BODY * 4) if method == "GET" else b""
                    return status, final, ctype, body
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    redirect_exc = exc
                    break  # follow it in the outer loop, do not retry
                if exc.code in _HTTP_RETRY_STATUSES and attempt == 0:
                    time.sleep(_HTTP_RETRY_BACKOFF)
                    continue
                raise
            except (urllib.error.URLError, OSError):
                if attempt == 0:
                    time.sleep(_HTTP_RETRY_BACKOFF)
                    continue
                raise

        # Reached only on a redirect: resolve Location and loop.
        location = redirect_exc.headers.get("Location") if redirect_exc else None
        if not location:
            raise redirect_exc  # type: ignore[misc]
        current = urllib.parse.urljoin(current, location)
        if redirect_exc.code == 303:
            method = "GET"
    raise ValueError(f"too many redirects: {url}")


def _fetch_one(url: str, level: str) -> Fetched:
    """Fetch a single URL at the given level (no archive fallback here)."""
    method = "GET" if level in ("L2", "L3") else "GET"
    _rate_limit(url)
    status, final, ctype, body = _open(url, method)
    f = Fetched(id=0, status=status, url_final=final, source="http")
    if level != "L1" and body:
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[-1].split(";")[0].strip() or "utf-8"
        try:
            text = body.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = body.decode("utf-8", errors="replace")
        title, meta, visible = _strip_html(text)
        f.title = title
        f.abstract = (meta + " " + visible).strip()[:MAX_BODY] if meta else visible
        if f.ok:
            f.soft_404_suspect = _detect_soft_404(f.title, f.abstract, url, final)
    return f


def fetch(citation: Citation, level: str = "L2") -> Fetched:
    """Fetch one citation's source via an explicit, observable fallback chain.

    Chain (each step tried only when the prior step fails), per the
    No-Silent-Fallback principle — the path that actually produced the data is
    recorded on ``Fetched.source`` so callers can see whether a citation was
    metadata-verified vs HTML-scraped vs archive vs unverifiable:

        1. Academic metadata API (Crossref / arXiv / NCBI) when the citation is
           an academic id (or a URL clearly carrying a DOI / arXiv / PubMed id).
           Bypasses paywalls; gives real title / authors / year / abstract.
        2. Direct HTTP fetch of the resolved URL (existing behaviour).
        3. Wayback Machine (web.archive.org).

    A failed metadata lookup never crashes the run — :mod:`sources` swallows
    network / parse errors and returns ``None``, and ``source`` then reflects the
    HTTP (or archive) path that actually served the data ("http", not "crossref").

    Args:
        citation: the citation to resolve and fetch.
        level: ``"L1"`` (status only), ``"L2"`` / ``"L3"`` (status + text).

    Returns:
        A :class:`Fetched` with ``id`` set to ``citation.id``.
    """
    url = _resolve_url(citation)

    # Step 1 — structured metadata API (additive: still status-check the URL).
    # Defensive: a metadata lookup must NEVER crash the run, so swallow anything
    # the source layer fails to (then fall through to HTTP / archive).
    try:
        meta, meta_source = _metadata_for(citation)
    except Exception:
        meta, meta_source = None, ""
    if meta is not None:
        # A successful metadata lookup is authoritative: the work exists and is
        # verifiable via the official API, so it counts as alive (status 200)
        # even when the publisher landing page is paywalled / bot-blocked
        # (403 / 404) — that is the whole point of the paywall bypass. We still
        # record the landing URL when a cheap status check resolves one.
        final = url
        landing_status: int | None = None
        try:
            landing_status, final, _ctype, _body = _open(url, "GET")
        except urllib.error.HTTPError as exc:
            # Dead/paywalled landing — keep the real code as an observable signal
            # rather than discarding it (No Silent Fallback).
            landing_status = exc.code
            final = url
        except (urllib.error.URLError, OSError, ValueError):
            final = url
        f = Fetched(
            id=citation.id,
            status=200,  # metadata fetched from the official API => alive
            title=meta.get("title", ""),
            abstract=meta.get("abstract", ""),
            url_final=final,
            authors=list(meta.get("authors") or []),
            year=meta.get("year"),
            source=meta_source,
            landing_status=landing_status,
        )
        return f

    # Step 2 — direct HTTP fetch.
    try:
        f = _fetch_one(url, level)
        f.id = citation.id
        return f
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        first_error = getattr(exc, "code", None) or str(exc)
        # Step 3 — Wayback Machine fallback.
        archive_url = "https://web.archive.org/web/" + url
        try:
            f = _fetch_one(archive_url, level)
            f.id = citation.id
            f.via_archive = True
            f.source = "archive"
            return f
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc2:
            status = getattr(exc, "code", 0) or 0
            return Fetched(
                id=citation.id,
                status=int(status) if isinstance(status, int) else 0,
                error=f"{first_error}; archive: {getattr(exc2, 'code', None) or exc2}",
                source="none",
            )


def fetch_all(
    citations: list[Citation], level: str = "L2", workers: int = 4
) -> dict[int, Fetched]:
    """Fetch all citations in parallel.

    Args:
        citations: citations to fetch.
        level: fetch level (see :func:`fetch`).
        workers: thread-pool size.

    Returns:
        ``{citation.id: Fetched}``.
    """
    results: dict[int, Fetched] = {}
    if not citations:
        return results
    level = level.upper()
    if level not in {"L1", "L2", "L3"}:
        raise ValueError(f"unknown level: {level!r} (expected L1 | L2 | L3)")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch, c, level): c for c in citations}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                results[c.id] = fut.result()
            except Exception as exc:  # defensive: never let one URL kill the run
                results[c.id] = Fetched(id=c.id, error=str(exc))
    return results
