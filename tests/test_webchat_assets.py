"""Web-chat assets must carry the exact score.py rubric — drift tripwire.

The rubric text exists in three places (code, README, web-chat prompts/skill).
These tests pin the prompts/skill to the code constants so a rubric change
cannot silently leave the web-chat copies stale.
"""

from pathlib import Path

import pytest

from paperverify.models import Verdict
from paperverify.score import _CLAIM_POINTS

ROOT = Path(__file__).resolve().parent.parent

ASSETS = [
    ROOT / "docs" / "webchat" / "webchat-prompt.md",
    ROOT / "docs" / "webchat" / "webchat-prompt.ko.md",
    ROOT / "integrations" / "skills" / "paper-verify-webchat" / "SKILL.md",
]

RUBRIC_ANCHORS = [
    f"Match = {_CLAIM_POINTS[Verdict.MATCH]}",
    f"Partial = {_CLAIM_POINTS[Verdict.PARTIAL]}",
    f"Uncertain = {_CLAIM_POINTS[Verdict.UNCERTAIN]}",
    f"Mismatch = {_CLAIM_POINTS[Verdict.MISMATCH]}",
    f"Inaccessible = {_CLAIM_POINTS[Verdict.INACCESSIBLE]}",
    "URL accessible | 20",
    "Author / year match | 20 / 10 / 0",
    "Cross-check agreement | 10",
    "90–100",
    "70–89",
    "50–69",
    "0–49",
]


@pytest.mark.parametrize("asset", ASSETS, ids=lambda p: str(p.relative_to(ROOT)))
def test_asset_exists(asset):
    assert asset.is_file(), f"missing web-chat asset: {asset}"


@pytest.mark.parametrize("asset", ASSETS, ids=lambda p: str(p.relative_to(ROOT)))
def test_rubric_anchors_present(asset):
    if not asset.is_file():
        pytest.fail(f"missing web-chat asset: {asset}")
    text = asset.read_text(encoding="utf-8")
    missing = [a for a in RUBRIC_ANCHORS if a not in text]
    assert not missing, f"{asset.name} missing rubric anchors: {missing}"


def test_skill_uses_offline_entrypoints():
    skill = ASSETS[2]
    if not skill.is_file():
        pytest.fail(f"missing web-chat asset: {skill}")
    text = skill.read_text(encoding="utf-8")
    assert "--from-evidence" in text
    assert "--extract-only" in text
