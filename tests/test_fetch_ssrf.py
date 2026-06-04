"""_open must refuse non-http(s) schemes and internal/loopback/metadata
addresses before issuing a request (audit SEC-01 / FR-09 / P0-7). This matters
when paper-verify runs as an MCP server / shared service where the document
author is not the operator.
"""

import pytest
import urllib.error

import paperverify.fetch as fetch_mod


def test_open_blocks_file_scheme():
    with pytest.raises(ValueError):
        fetch_mod._open("file:///etc/passwd", "GET")


def test_open_blocks_ftp_scheme():
    with pytest.raises(ValueError):
        fetch_mod._open("ftp://example.com/x", "GET")


def test_open_blocks_cloud_metadata_address():
    with pytest.raises(ValueError):
        fetch_mod._open("http://169.254.169.254/latest/meta-data/", "GET")


def test_open_blocks_loopback_address():
    with pytest.raises(ValueError):
        fetch_mod._open("http://127.0.0.1:8080/admin", "GET")


def test_open_blocks_redirect_to_loopback(monkeypatch):
    class RedirectingOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(
                req.full_url,
                302,
                "Found",
                {"Location": "http://127.0.0.1:8080/admin"},
                None,
            )

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *handlers: RedirectingOpener())

    with pytest.raises(ValueError, match="blocked internal address"):
        fetch_mod._open("https://example.com/redirect", "GET")


def test_open_blocks_redirect_to_file_scheme(monkeypatch):
    class RedirectingOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(
                req.full_url,
                302,
                "Found",
                {"Location": "file:///etc/passwd"},
                None,
            )

    monkeypatch.setattr(fetch_mod.urllib.request, "build_opener", lambda *handlers: RedirectingOpener())

    with pytest.raises(ValueError, match="blocked URL scheme"):
        fetch_mod._open("https://example.com/redirect", "GET")
