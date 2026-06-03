"""MCP server wrapper for paper-verify.

Exposes the citation-verification pipeline as Model Context Protocol tools so
any MCP-capable agent (Claude Code, Codex, etc.) can call paper-verify as a
tool over stdio.

The ``mcp`` package is an **optional** dependency. Importing this module
without it installed raises a clear, actionable :class:`RuntimeError` with the
install hint rather than a bare ``ImportError`` — the core tool and the CLI
``--json`` surface keep working with ``mcp`` not installed.

Install:  ``pip install paper-verify[mcp]``
Run:      ``paper-verify-mcp``           (console script, stdio transport)
          ``python -m paperverify.mcp_server``
"""

from __future__ import annotations

from .cli import run_pipeline
from .extract import extract
from .harness import get_profile as _get_profile
from .harness import list_profiles as _list_profiles
from .report import render_json

_MCP_INSTALL_HINT = (
    "the 'mcp' extra is required to run the MCP server: "
    "pip install paper-verify[mcp]"
)


def _import_fastmcp():
    """Lazily import FastMCP, converting a missing dep into a helpful error."""
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: PLC0415  (lazy optional import)
    except ImportError as exc:  # pragma: no cover - exercised only without mcp
        raise RuntimeError(_MCP_INSTALL_HINT) from exc
    return FastMCP


def build_server():
    """Construct and return the FastMCP server with paper-verify tools registered.

    Kept separate from :func:`main` so tests / clients can introspect the
    registered tools without starting the stdio loop.
    """
    FastMCP = _import_fastmcp()
    mcp = FastMCP(name="paper-verify")

    @mcp.tool()
    def verify_file(
        path: str,
        level: str = "L2",
        judges: list[str] | None = None,
        workers: int = 4,
        tiebreak: str | None = None,
    ) -> dict:
        """Verify all citations in a document file.

        Runs the full extract -> fetch -> judge -> score pipeline on the file at
        ``path`` and returns the same structured dict as the CLI ``--json``
        output (schema_version, overall_score/tier, has_failure,
        tier_distribution, and a per-citation array).

        Args:
            path: path to a Markdown / text file to verify.
            level: "L1" (HTTP only), "L2" (abstract/title match, default),
                or "L3" (full content).
            judges: judge specs, e.g. ["keyword"], ["anthropic:claude-sonnet-4-6"].
                Defaults to ["keyword"] (no network LLM / no API key needed).
            workers: parallel fetch workers.
            tiebreak: optional judge spec used only to break a tie when 2+
                ``judges`` disagree on a citation (default None = no tie-break).
        """
        from pathlib import Path

        text = Path(path).read_text(encoding="utf-8", errors="replace")
        report = run_pipeline(
            text,
            source_file=path,
            level=level,
            judge_specs=judges or ["keyword"],
            workers=workers,
            tiebreak_spec=tiebreak,
        )
        return render_json(report)

    @mcp.tool()
    def verify_text(
        text: str,
        level: str = "L2",
        judges: list[str] | None = None,
        tiebreak: str | None = None,
    ) -> dict:
        """Verify citations in raw document text (no file needed).

        Same pipeline and return shape as ``verify_file`` but takes the document
        contents directly as a string.

        Args:
            text: raw document text to extract citations from and verify.
            level: "L1" | "L2" (default) | "L3".
            judges: judge specs (default ["keyword"]).
            tiebreak: optional judge spec used only to break a tie when 2+
                ``judges`` disagree on a citation (default None = no tie-break).
        """
        report = run_pipeline(
            text,
            source_file="<text>",
            level=level,
            judge_specs=judges or ["keyword"],
            tiebreak_spec=tiebreak,
        )
        return render_json(report)

    @mcp.tool()
    def extract_citations(text: str) -> list[dict]:
        """Extract citations from text — no network, no LLM.

        Returns the list of detected citations (type, ref, context, line) without
        fetching sources or judging claims. Useful as a cheap pre-check.

        Args:
            text: raw document text.
        """
        return [c.to_dict() for c in extract(text)]

    @mcp.tool()
    def list_profiles() -> list[dict]:
        """List the harness profiles so an agent can self-discover its setup.

        Returns one dict per supported frontend (Claude Code, Cursor, Codex,
        Gemini) with keys: key, display_name, frontend, primary_surface,
        skill_surface, invocation, recommended_judges, config_files, strengths,
        cautions. Single source of truth — same data the CLI ``--list-profiles``
        flag emits.
        """
        return [p.to_dict() for p in _list_profiles()]

    @mcp.tool()
    def get_profile(key: str) -> dict:
        """Look up one harness profile by key or alias.

        Accepts profile keys (``claude-code``, ``cursor``, ``codex``,
        ``gemini``) and aliases (e.g. ``claude``). Returns the profile dict, or
        ``{"error": "..."}`` for an unknown key.

        Args:
            key: profile key or alias.
        """
        try:
            return _get_profile(key).to_dict()
        except ValueError as exc:
            return {"error": str(exc)}

    return mcp


def main() -> None:
    """Console-script entrypoint: run the server over stdio transport."""
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
