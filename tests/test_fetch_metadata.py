"""Metadata-API hits must preserve the landing URL's real status as an
observable signal (audit FR-03 / FR-05 / SA-03 / P0-3). The work still counts
as alive (paywall-bypass intent), but a dead/retracted landing (404) must no
longer be silently discarded — No Silent Fallback.
"""

import urllib.error

import paperverify.fetch as fetch_mod
from paperverify.models import Citation


def _doi_cite():
    return Citation(id=1, type="DOI", ref="10.1/x", context="ctx", line=1)


def _meta(_c):
    return ({"title": "T", "abstract": "A", "authors": ["X"], "year": 2020}, "crossref")


def test_metadata_hit_records_dead_landing_status(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_metadata_for", _meta)

    def _raise_404(url, method):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(fetch_mod, "_open", _raise_404)
    f = fetch_mod.fetch(_doi_cite(), "L2")
    assert f.source == "crossref"
    assert f.status == 200            # alive via official API (intent preserved)
    assert f.landing_status == 404    # dead landing now observable


def test_metadata_hit_records_ok_landing_status(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_metadata_for", _meta)
    monkeypatch.setattr(fetch_mod, "_open", lambda url, method: (200, url, "text/html", b"ok"))
    f = fetch_mod.fetch(_doi_cite(), "L2")
    assert f.status == 200
    assert f.landing_status == 200
