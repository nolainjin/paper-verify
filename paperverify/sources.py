"""Academic metadata APIs — Crossref / arXiv / NCBI E-utilities (stdlib only).

For academic identifiers (DOI / arXiv / PMID / PMC) these free, official APIs
return **structured metadata** (title, authors, year, abstract) directly. That
bypasses publisher paywalls (you still get the abstract + authors + year even
when the HTML landing page is a paywall stub) and makes author/year scoring
real instead of a fuzzy string match against scraped HTML.

Pure stdlib: ``urllib`` + ``json`` + ``xml.etree``. No third-party deps.

Each ``fetch_*_metadata`` function returns a normalized dict::

    {"title": str, "authors": list[str], "year": int | None, "abstract": str}

or ``None`` when the lookup fails (network error, parse error, or the record
does not exist). Callers treat ``None`` as "fall through to the next source"
(see :mod:`paperverify.fetch` for the explicit fallback chain). These functions
never raise for network / parse problems — they swallow and return ``None`` so a
single bad identifier can never crash a run (No Silent Fallback: the *caller*
records which path actually produced data via ``Fetched.source``).

API contracts relied on (confirmed against live responses, 2026-06):
    Crossref  GET https://api.crossref.org/works/{doi}
              message.title[] · message.author[].{family,given}
              message.issued.date-parts[[YYYY,M,D]] (or .published) · message.abstract (JATS)
    arXiv     GET http://export.arxiv.org/api/query?id_list={id}
              Atom: feed/entry/{title, author/name, summary, published}
    NCBI      GET .../esummary.fcgi?db=pubmed&id={pmid}&retmode=json
              result.{id}.{title, authors[].name, pubdate}
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from xml.etree import ElementTree as ET

# A descriptive User-Agent with a contact mailto puts Crossref/NCBI requests in
# the "polite pool" and satisfies their fair-use guidance.
CONTACT_EMAIL = "paper-verify@users.noreply.github.com"
API_USER_AGENT = f"paper-verify/0.1 (https://github.com/nolainjin/paper-verify; mailto:{CONTACT_EMAIL})"
API_TIMEOUT = 8  # seconds — short, so a slow API does not stall the whole run
# Upper bound on bytes read from any metadata API response. These endpoints
# return small JSON / Atom records (a few KB); a 2 MB cap is generous and stops
# a hostile or misbehaving endpoint from streaming an unbounded body into memory
# (audit P1-8 / SEC-04, DoS). XML parsing below stays on stdlib ElementTree; the
# input is now size-bounded and comes only from the fixed official-API hosts, so
# the classic entity-expansion / billion-laughs vector is mitigated by the cap
# (a defusedxml dependency was considered but rejected to keep the core stdlib-only).
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}

# JATS / HTML tags that may wrap a Crossref abstract.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class _NotFound(Exception):
    """The API responded but the record does not exist (HTTP 404) — no retry."""


def _clean_abstract(raw: str) -> str:
    """Strip JATS/HTML tags and collapse whitespace from a Crossref abstract."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    return _WS_RE.sub(" ", text).strip()


def _get(url: str, *, accept: str = "*/*") -> bytes:
    """GET ``url`` with one retry/backoff on transient errors.

    Raises:
        _NotFound: the API returned HTTP 404 (record absent — do not retry).
        Exception: any other failure after the retry is exhausted; callers
            catch broadly and treat it as "this source produced nothing".
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": API_USER_AGENT, "Accept": accept}
    )
    last_exc: Optional[BaseException] = None
    for attempt in range(2):  # 1 initial try + 1 retry
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                # Cap the read so a hostile / runaway endpoint cannot exhaust
                # memory (P1-8 / SEC-04). Read one byte past the cap to detect
                # (and drop) over-long bodies deterministically.
                return resp.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _NotFound(url) from exc
            last_exc = exc
            if exc.code not in _RETRY_STATUSES:
                raise
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc  # transient: timeout / connection refused
        if attempt == 0:
            time.sleep(0.5)  # small backoff before the single retry
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------


def fetch_doi_metadata(doi: str) -> Optional[dict]:
    """Fetch structured metadata for a DOI from the Crossref REST API."""
    doi = doi.strip()
    if not doi:
        return None
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    try:
        raw = _get(url, accept="application/json")
        msg = json.loads(raw.decode("utf-8", errors="replace")).get("message", {})
    except _NotFound:
        return None
    except Exception:  # network / decode / JSON error — fall through
        return None
    if not isinstance(msg, dict):
        return None

    titles = msg.get("title") or []
    title = titles[0] if isinstance(titles, list) and titles else (titles if isinstance(titles, str) else "")

    authors: list[str] = []
    for a in msg.get("author") or []:
        if not isinstance(a, dict):
            continue
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        name = (f"{given} {family}".strip()) or (a.get("name") or "").strip()
        if name:
            authors.append(name)

    year = _crossref_year(msg.get("issued")) or _crossref_year(msg.get("published"))
    abstract = _clean_abstract(msg.get("abstract") or "")

    if not (title or authors or abstract):
        return None
    return {"title": title.strip(), "authors": authors, "year": year, "abstract": abstract}


def _crossref_year(node: object) -> Optional[int]:
    """Extract the year from a Crossref date node: {"date-parts": [[YYYY,...]]}."""
    if not isinstance(node, dict):
        return None
    parts = node.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def fetch_arxiv_metadata(arxiv_id: str) -> Optional[dict]:
    """Fetch structured metadata for an arXiv id from the arXiv Atom API."""
    ident = arxiv_id.strip()
    # Accept "arXiv:1706.03762" / "arxiv:1706.03762" / bare id.
    if ":" in ident:
        ident = ident.split(":", 1)[-1].strip()
    if not ident:
        return None
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ident})
    try:
        raw = _get(url, accept="application/atom+xml")
        root = ET.fromstring(raw)
    except _NotFound:
        return None
    except Exception:  # network / XML parse error — fall through
        return None

    entry = root.find("atom:entry", _ARXIV_NS)
    if entry is None:
        return None

    # arXiv returns HTTP 200 with an error <entry> for unknown/malformed ids
    # (id -> arxiv.org/api/errors, title "Error"). Do not treat as a real paper.
    id_el = entry.find("atom:id", _ARXIV_NS)
    if id_el is not None and id_el.text and "arxiv.org/api/errors" in id_el.text:
        return None

    title_el = entry.find("atom:title", _ARXIV_NS)
    title = (title_el.text or "").strip() if title_el is not None else ""

    authors: list[str] = []
    for author_el in entry.findall("atom:author", _ARXIV_NS):
        name_el = author_el.find("atom:name", _ARXIV_NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    summary_el = entry.find("atom:summary", _ARXIV_NS)
    abstract = _WS_RE.sub(" ", (summary_el.text or "")).strip() if summary_el is not None else ""

    year: Optional[int] = None
    published_el = entry.find("atom:published", _ARXIV_NS)
    if published_el is not None and published_el.text and len(published_el.text) >= 4:
        try:
            year = int(published_el.text[:4])
        except ValueError:
            year = None

    if not (title or authors or abstract):
        return None
    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


# ---------------------------------------------------------------------------
# NCBI E-utilities (PubMed / PMC)
# ---------------------------------------------------------------------------


def fetch_pmid_metadata(pmid: str) -> Optional[dict]:
    """Fetch structured metadata for a PMID from NCBI E-utilities (esummary)."""
    digits = "".join(c for c in pmid if c.isdigit())
    if not digits:
        return None
    params = {
        "db": "pubmed",
        "id": digits,
        "retmode": "json",
        "tool": "paper-verify",
        "email": CONTACT_EMAIL,
    }
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(params)
    try:
        raw = _get(url, accept="application/json")
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except _NotFound:
        return None
    except Exception:  # network / decode / JSON error — fall through
        return None

    rec = (data.get("result") or {}).get(digits)
    if not isinstance(rec, dict) or rec.get("error"):
        return None

    title = (rec.get("title") or "").strip()
    authors = [
        a["name"].strip()
        for a in (rec.get("authors") or [])
        if isinstance(a, dict) and a.get("name")
    ]

    year: Optional[int] = None
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", rec.get("pubdate") or "")
    if m:
        year = int(m.group(1))

    # esummary carries no abstract; efetch would, but title+authors+year is
    # enough to make author/year scoring real. Leave abstract empty (the HTML
    # fetch fallback may still populate it for L2/L3 claim matching).
    if not (title or authors):
        return None
    return {"title": title, "authors": authors, "year": year, "abstract": ""}


def fetch_pmc_metadata(pmc_id: str) -> Optional[dict]:
    """Fetch metadata for a PMC id by converting it to a PMID via NCBI idconv."""
    ident = pmc_id.strip()
    if not ident.upper().startswith("PMC"):
        ident = "PMC" + "".join(c for c in ident if c.isdigit())
    if ident == "PMC":
        return None
    params = {
        "ids": ident,
        "format": "json",
        "tool": "paper-verify",
        "email": CONTACT_EMAIL,
    }
    url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?" + urllib.parse.urlencode(params)
    try:
        raw = _get(url, accept="application/json")
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except _NotFound:
        return None
    except Exception:
        return None
    records = data.get("records") or []
    pmid = None
    for r in records:
        if isinstance(r, dict) and r.get("pmid"):
            pmid = r["pmid"]
            break
    if not pmid:
        return None
    return fetch_pmid_metadata(str(pmid))
