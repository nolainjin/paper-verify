"""Provider-specific harness profiles.

The core verifier stays provider-neutral. Harness profiles capture the frontend
contract for Claude Code, Cursor, Codex, and Gemini so docs, skills, and future
adapters can share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessProfile:
    """Static strategy contract for an agent frontend."""

    key: str
    display_name: str
    frontend: str
    primary_surface: str
    skill_surface: str
    invocation: tuple[str, ...]
    recommended_judges: tuple[str, ...]
    config_files: tuple[str, ...]
    strengths: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict:
        """Return a JSON-serializable profile."""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "frontend": self.frontend,
            "primary_surface": self.primary_surface,
            "skill_surface": self.skill_surface,
            "invocation": list(self.invocation),
            "recommended_judges": list(self.recommended_judges),
            "config_files": list(self.config_files),
            "strengths": list(self.strengths),
            "cautions": list(self.cautions),
        }


_PROFILES: dict[str, HarnessProfile] = {
    "claude-code": HarnessProfile(
        key="claude-code",
        display_name="Claude Code",
        frontend="Claude Code",
        primary_surface="MCP stdio tool server",
        skill_surface="Claude project instructions and slash-command workflow",
        invocation=(
            "pip install paper-verify[mcp]",
            "claude mcp add paper-verify -- paper-verify-mcp",
        ),
        recommended_judges=("anthropic", "keyword"),
        config_files=("CLAUDE.md", ".claude/agents/*.md", "mcpServers"),
        strengths=(
            "Best fit for tool-calling verification loops.",
            "Can keep paper-verify as a reusable MCP tool across projects.",
        ),
        cautions=(
            "Document the expected JSON fields so Claude does not infer scores from prose.",
            "Keep expensive L2/L3 checks behind an explicit user action.",
        ),
    ),
    "cursor": HarnessProfile(
        key="cursor",
        display_name="Cursor",
        frontend="Cursor",
        primary_surface="CLI JSON command",
        skill_surface="Workspace rules and composer instructions",
        invocation=(
            "paper-verify <file> --level L2 --judge keyword --json",
            "paper-verify <file> --level L2 --judge openai --json",
        ),
        recommended_judges=("openai", "keyword"),
        config_files=(".cursor/rules", ".cursorrules", "README.md"),
        strengths=(
            "Simple fit for editor-side checks on the current file.",
            "JSON stdout is easy to pipe into Cursor workflows.",
        ),
        cautions=(
            "Prefer file-scoped checks to avoid long-running editor commands.",
            "Keep rule text short because Cursor may blend it with coding instructions.",
        ),
    ),
    "codex": HarnessProfile(
        key="codex",
        display_name="Codex",
        frontend="Codex",
        primary_surface="CLI JSON plus optional MCP",
        skill_surface="AGENTS.md and Codex skill instructions",
        invocation=(
            "paper-verify <file> --level L1 --json",
            "paper-verify <file> --level L2 --judge keyword --json",
        ),
        recommended_judges=("keyword", "openai", "cli:codex"),
        config_files=("AGENTS.md", "skills/*/SKILL.md", "pyproject.toml"),
        strengths=(
            "Good fit for repository scans, test-backed fixes, and PR preparation.",
            "Can treat has_failure and tier_distribution as gating fields.",
        ),
        cautions=(
            "Avoid provider-specific branches; use profiles and docs instead.",
            "Keep generated reports out of commits unless the user asks for artifacts.",
        ),
    ),
    "gemini": HarnessProfile(
        key="gemini",
        display_name="Gemini",
        frontend="Gemini",
        primary_surface="CLI judge or JSON command",
        skill_surface="Prompt contract and long-context review workflow",
        invocation=(
            "paper-verify <file> --level L2 --judge cli:gemini --json",
            "paper-verify <file> --level L2 --judge gemini --json",
        ),
        recommended_judges=("gemini", "cli:gemini", "keyword"),
        config_files=("GEMINI.md", "README.md", "prompt-library.md"),
        strengths=(
            "Useful for broad context review and second-opinion citation checks.",
            "Can run as an independent judge in cross-check mode.",
        ),
        cautions=(
            "CLI behavior varies by local Gemini setup, so keep fallback judge specs documented.",
            "Require strict JSON parsing from paper-verify output, not narrative summaries.",
        ),
    ),
}


def list_profiles() -> list[HarnessProfile]:
    """Return all supported harness profiles in stable order."""
    return [_PROFILES[key] for key in sorted(_PROFILES)]


def get_profile(key: str) -> HarnessProfile:
    """Return one profile by key or display name."""
    normalized = key.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "claude": "claude-code",
        "claudecode": "claude-code",
        "claude-code": "claude-code",
        "cursor": "cursor",
        "codex": "codex",
        "gemini": "gemini",
    }
    profile_key = aliases.get(normalized, normalized)
    try:
        return _PROFILES[profile_key]
    except KeyError as exc:
        known = ", ".join(sorted(_PROFILES))
        raise ValueError(f"unknown harness profile: {key!r} (expected one of: {known})") from exc

