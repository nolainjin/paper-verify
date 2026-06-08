"""Characterization coverage for paperverify.mcp_server.

Targets what test_mcp_server.py leaves open: the ``verify_file_impl`` OSError
path (stat failure), and the whole ``build_server`` / ``main`` surface (lines
138-238) which only run when the optional ``mcp`` package is importable. We
inject a minimal fake ``mcp.server.fastmcp.FastMCP`` into ``sys.modules`` so
``build_server`` registers its tools and we can invoke each registered wrapper —
no real ``mcp`` package, no stdio loop, no network.

All deterministic: file I/O is tmp_path only; the pipeline runs at L1 on
citation-free text (no fetch / no LLM).
"""

import sys
import types

import pytest

from paperverify import mcp_server


# ---------------------------------------------------------------------------
# verify_file_impl — stat() OSError path (lines 74-75)
# ---------------------------------------------------------------------------


def test_verify_file_stat_oserror_is_structured(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("text")

    real_stat = mcp_server.Path.stat

    def boom_stat(self, *a, **k):
        if self == doc:
            raise OSError("permission denied")
        return real_stat(self, *a, **k)

    # is_file() must still pass so we reach the stat() call.
    monkeypatch.setattr(mcp_server.Path, "stat", boom_stat)
    out = mcp_server.verify_file_impl(str(doc))
    assert "error" in out
    assert "cannot access" in out["error"]
    assert "permission denied" in out["error"]


# ---------------------------------------------------------------------------
# A minimal FastMCP stand-in so build_server() + tool wrappers run without the
# optional ``mcp`` package (lines 138-228).
# ---------------------------------------------------------------------------


class _FakeFastMCP:
    def __init__(self, name=None):
        self.name = name
        self.tools = {}
        self.ran_transport = None

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco

    def run(self, transport=None):
        self.ran_transport = transport


@pytest.fixture
def fake_mcp(monkeypatch):
    """Install a fake ``mcp.server.fastmcp`` so _import_fastmcp() succeeds."""
    mcp_pkg = types.ModuleType("mcp")
    server_pkg = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    server_pkg.fastmcp = fastmcp_mod  # type: ignore[attr-defined]
    mcp_pkg.server = server_pkg  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server", server_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)
    return fastmcp_mod


# ---------------------------------------------------------------------------
# _import_fastmcp — success branch (line 51)
# ---------------------------------------------------------------------------


def test_import_fastmcp_success_returns_class(fake_mcp):
    assert mcp_server._import_fastmcp() is _FakeFastMCP


# ---------------------------------------------------------------------------
# build_server — registers all five tools, each wrapper delegates correctly.
# ---------------------------------------------------------------------------


def test_build_server_registers_all_tools(fake_mcp):
    server = mcp_server.build_server()
    assert isinstance(server, _FakeFastMCP)
    assert server.name == "paper-verify"
    assert set(server.tools) == {
        "verify_file",
        "verify_text",
        "extract_citations",
        "list_profiles",
        "get_profile",
    }


def test_build_server_verify_text_tool_runs_pipeline(fake_mcp):
    server = mcp_server.build_server()
    out = server.tools["verify_text"]("No citations here.", level="L1")
    assert out["schema_version"] == "4"
    assert out["source_file"] == "<text>"


def test_build_server_verify_file_tool_delegates(fake_mcp, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("A note with no citations.\n")
    server = mcp_server.build_server()
    out = server.tools["verify_file"](str(doc), level="L1")
    assert out["schema_version"] == "4"
    assert out["source_file"] == str(doc)


def test_build_server_verify_file_tool_threads_args(fake_mcp, tmp_path, monkeypatch):
    # Confirm the wrapper passes workers/judges/tiebreak through to the impl.
    doc = tmp_path / "doc.md"
    doc.write_text("x")
    captured = {}

    def fake_impl(path, level="L2", judges=None, workers=4, tiebreak=None):
        captured.update(path=path, level=level, judges=judges, workers=workers, tiebreak=tiebreak)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "verify_file_impl", fake_impl)
    server = mcp_server.build_server()
    out = server.tools["verify_file"](
        str(doc), level="L3", judges=["keyword"], workers=7, tiebreak="cli:gemini"
    )
    assert out == {"ok": True}
    assert captured == {
        "path": str(doc),
        "level": "L3",
        "judges": ["keyword"],
        "workers": 7,
        "tiebreak": "cli:gemini",
    }


def test_build_server_extract_citations_tool(fake_mcp):
    server = mcp_server.build_server()
    out = server.tools["extract_citations"]("See https://example.com/a here.")
    assert any(c["type"] == "URL" for c in out)


def test_build_server_list_profiles_tool(fake_mcp):
    server = mcp_server.build_server()
    out = server.tools["list_profiles"]()
    assert out and all("key" in p for p in out)


def test_build_server_get_profile_tool_known_and_unknown(fake_mcp):
    server = mcp_server.build_server()
    assert server.tools["get_profile"]("claude")["key"] == "claude-code"
    assert "error" in server.tools["get_profile"]("nope")


# ---------------------------------------------------------------------------
# main — entrypoint runs the server over stdio (lines 233-234, 238 guard)
# ---------------------------------------------------------------------------


def test_main_builds_and_runs_stdio(fake_mcp, monkeypatch):
    built = {}

    def fake_build():
        srv = _FakeFastMCP(name="paper-verify")
        built["srv"] = srv
        return srv

    monkeypatch.setattr(mcp_server, "build_server", fake_build)
    mcp_server.main()
    assert built["srv"].ran_transport == "stdio"
