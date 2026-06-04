"""_open must refuse non-http(s) schemes and internal/loopback/metadata
addresses before issuing a request (audit SEC-01 / FR-09 / P0-7). This matters
when paper-verify runs as an MCP server / shared service where the document
author is not the operator.
"""

import pytest

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
