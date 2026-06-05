"""Tests for frontend harness profiles."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from paperverify.harness import get_profile, list_profiles
from paperverify.judge import make_judge

ROOT = Path(__file__).resolve().parents[1]

_PROFILE_DICT_KEYS = {
    "key",
    "display_name",
    "frontend",
    "primary_surface",
    "skill_surface",
    "invocation",
    "recommended_judges",
    "config_files",
    "strengths",
    "cautions",
}


def test_list_profiles_has_expected_frontends():
    keys = {profile.key for profile in list_profiles()}
    assert keys == {"claude-code", "cursor", "codex", "gemini"}


def test_profile_aliases_return_same_contract():
    assert get_profile("Claude").key == "claude-code"
    assert get_profile("claude_code").key == "claude-code"
    assert get_profile("Codex").display_name == "Codex"


def test_profiles_are_json_serializable_shape():
    data = get_profile("cursor").to_dict()
    assert data["key"] == "cursor"
    assert data["invocation"]
    assert data["recommended_judges"]
    assert data["config_files"]


def test_unknown_profile_raises_clear_error():
    with pytest.raises(ValueError, match="unknown harness profile"):
        get_profile("unknown")


# --- Drift guard: profile metadata must match the real judge factory ---------


def test_recommended_judges_are_known_specs():
    """Every recommended judge spec must be a known shape to make_judge.

    Guards against profile metadata drifting away from the judge factory.
    We only *construct* the judge (no .evaluate, so no network / API keys).
    """
    for profile in list_profiles():
        for spec in profile.recommended_judges:
            # Must not raise ValueError (unknown spec shape).
            judge = make_judge(spec)
            assert judge is not None, f"{profile.key}: {spec!r} built no judge"


# --- CLI wiring (network-free) -----------------------------------------------


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "paperverify.cli", *argv],
        capture_output=True,
        text=True,
    )


def test_cli_list_profiles_emits_valid_json():
    proc = _run_cli("--list-profiles")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert isinstance(data, list) and data
    keys = {entry["key"] for entry in data}
    assert keys == {"claude-code", "cursor", "codex", "gemini"}
    for entry in data:
        assert set(entry.keys()) == _PROFILE_DICT_KEYS


def test_cli_profile_records_active_profile(tmp_path):
    src = tmp_path / "sample.md"
    src.write_text("No citations here.", encoding="utf-8")
    proc = _run_cli(str(src), "--profile", "codex", "--level", "L1", "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["profile"] == "codex"
    # L1 needs no judge / network.
    assert data["level"] == "L1"
    assert data["judges"] == []


def test_cli_explicit_judge_wins_over_profile(tmp_path):
    # No citations => no fetch / no network. With zero scored citations the
    # report still reports the constructed judges, so we can assert judge wiring
    # without touching the network. claude-code recommends "anthropic" first;
    # explicit --judge keyword must win regardless.
    src = tmp_path / "sample.md"
    src.write_text("No citations here, just prose.", encoding="utf-8")
    proc = _run_cli(
        str(src),
        "--profile", "claude-code",
        "--judge", "keyword",
        "--level", "L2",
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["profile"] == "claude-code"
    # Explicit --judge keyword wins; the profile's recommended judges do not override.
    assert data["judges"] == ["keyword"]


def test_phase2_skill_artifact_matches_triage_contract():
    skill = (ROOT / "integrations/skills/paper-verify/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "name: paper-verify" in skill
    assert "paper-verify <file> --level L1 --json" in skill
    assert "effective_verdict" in skill
    assert "Must Review" in skill


def test_phase3_plugin_manifest_links_skill_and_mcp():
    manifest = json.loads(
        (ROOT / "integrations/plugins/paper-verify/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    mcp = json.loads(
        (ROOT / "integrations/plugins/paper-verify/.mcp.json").read_text(
            encoding="utf-8"
        )
    )
    skill = ROOT / "integrations/plugins/paper-verify/skills/paper-verify/SKILL.md"
    agent_meta = (
        ROOT / "integrations/plugins/paper-verify/skills/paper-verify/agents/openai.yaml"
    )

    assert manifest["name"] == "paper-verify"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert skill.exists()
    assert agent_meta.exists()
    assert mcp["mcpServers"]["paper-verify"]["command"] == "paper-verify-mcp"
