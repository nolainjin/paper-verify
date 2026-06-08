"""mcp_server coverage + robustness (audit P1-10 coverage / P1-7 MC-1·MC-4·SEC-02).

mcp_server.py was 0% covered. The MCP tool surface must not crash the server on
bad input: a missing/oversized file or a pipeline error should come back as a
structured ``{"error": ...}`` dict, not a raw exception. These tests exercise
the tool *implementations* directly (no ``mcp`` package needed); build_server
is covered only when the optional ``mcp`` extra is installed.
"""

import sys

import pytest

from paperverify import mcp_server


# --- verify_text_impl ------------------------------------------------------

def test_verify_text_returns_json_dict():
    out = mcp_server.verify_text_impl("No citations here.", level="L1")
    assert out["schema_version"] == "5"
    assert out["source_file"] == "<text>"
    assert out["level"] == "L1"
    assert isinstance(out["citations"], list)


def test_verify_text_pipeline_error_is_structured(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad level")
    monkeypatch.setattr(mcp_server, "run_pipeline", boom)
    out = mcp_server.verify_text_impl("x", level="L9")
    assert "error" in out
    assert "bad level" in out["error"]


# --- verify_file_impl: path validation (SEC-02 / MC-4) ---------------------

def test_verify_file_missing_path_errors(tmp_path):
    out = mcp_server.verify_file_impl(str(tmp_path / "nope.md"))
    assert "error" in out
    assert "not a file" in out["error"]


def test_verify_file_directory_errors(tmp_path):
    out = mcp_server.verify_file_impl(str(tmp_path))
    assert "error" in out
    assert "not a file" in out["error"]


def test_verify_file_too_large_errors(tmp_path, monkeypatch):
    big = tmp_path / "big.md"
    big.write_text("data")
    monkeypatch.setattr(mcp_server, "_MAX_FILE_BYTES", 1)
    out = mcp_server.verify_file_impl(str(big))
    assert "error" in out
    assert "too large" in out["error"]


def test_verify_file_happy_path(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("A plain note with no citations.\n")
    out = mcp_server.verify_file_impl(str(doc), level="L1")
    assert out["schema_version"] == "5"
    assert out["source_file"] == str(doc)


def test_verify_file_pipeline_error_is_structured(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("text")

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(mcp_server, "run_pipeline", boom)
    out = mcp_server.verify_file_impl(str(doc))
    assert "error" in out
    assert "disk gone" in out["error"]


# --- extract_citations_impl ------------------------------------------------

def test_extract_citations_impl_finds_url():
    out = mcp_server.extract_citations_impl("See https://example.com/a for details.")
    assert isinstance(out, list)
    assert any(c["type"] == "URL" for c in out)


# --- profile tools ---------------------------------------------------------

def test_list_profiles_impl():
    out = mcp_server.list_profiles_impl()
    assert isinstance(out, list) and out
    assert all("key" in p for p in out)


def test_get_profile_impl_known():
    out = mcp_server.get_profile_impl("claude")  # alias
    assert "error" not in out
    assert out["key"] == "claude-code"


def test_get_profile_impl_unknown_returns_error():
    out = mcp_server.get_profile_impl("does-not-exist")
    assert "error" in out


# --- _import_fastmcp error path (exercised without mcp installed) -----------

def test_import_fastmcp_missing_raises_runtime(monkeypatch):
    # Force the import to fail regardless of whether mcp is installed.
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match="mcp"):
        mcp_server._import_fastmcp()


# --- build_server: only when the optional mcp extra is present -------------

def test_build_server_registers_tools_when_mcp_available():
    pytest.importorskip("mcp.server.fastmcp")
    server = mcp_server.build_server()
    assert server is not None
    assert getattr(server, "name", None) == "paper-verify"
