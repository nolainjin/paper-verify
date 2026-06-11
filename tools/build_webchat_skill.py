#!/usr/bin/env python3
"""Build the claude.ai-uploadable web-chat skill zip.

Bundles the skill instructions with the stdlib-only ``paperverify`` package so
the claude.ai sandbox can run extraction and rubric scoring as code (the model
does fetching/judging with its own web tools — see the SKILL.md workflow).

Usage:
    python tools/build_webchat_skill.py            # -> dist/paper-verify-webchat-skill.zip
    python tools/build_webchat_skill.py --out DIR  # custom output dir
    python tools/build_webchat_skill.py --check    # build to a temp dir, verify members
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "integrations" / "skills" / "paper-verify-webchat"
ZIP_NAME = "paper-verify-webchat-skill.zip"
TOP = "paper-verify-webchat"  # claude.ai expects a folder with SKILL.md at its root


def _members() -> list[tuple[str, Path]]:
    members = [(f"{TOP}/SKILL.md", SKILL_DIR / "SKILL.md")]
    pkg = ROOT / "paperverify"
    for py in sorted(pkg.rglob("*.py")):
        members.append((f"{TOP}/paperverify/{py.relative_to(pkg)}", py))
    members.append(
        (f"{TOP}/examples/evidence-sample.json", ROOT / "examples" / "evidence-sample.json")
    )
    members.append((f"{TOP}/fallback-prompt.md", ROOT / "docs" / "webchat" / "webchat-prompt.md"))
    return members


def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / ZIP_NAME
    members = _members()
    missing = [str(src) for _, src in members if not src.is_file()]
    if missing:
        raise SystemExit(f"missing source files: {', '.join(missing)}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, src in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # reproducible
            info.external_attr = 0o644 << 16
            zf.writestr(info, src.read_bytes())
    return zip_path


def check(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    required = {
        f"{TOP}/SKILL.md",
        f"{TOP}/paperverify/__main__.py",
        f"{TOP}/paperverify/offline.py",
        f"{TOP}/examples/evidence-sample.json",
        f"{TOP}/fallback-prompt.md",
    }
    missing = required - names
    if missing:
        print(f"FAIL: zip missing {sorted(missing)}", file=sys.stderr)
        return 1
    print(f"OK: {zip_path} ({len(names)} files)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument(
        "--check", action="store_true", help="build into a temp dir and verify required members"
    )
    args = ap.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as td:
            return check(build(Path(td)))
    return check(build(Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
