#!/usr/bin/env python3
"""Pass-state gate for feature_list.json (learn-harness-engineering L08 primitive).

The point of a feature list as a *harness primitive* (not a human note) is that
the only path from any state to ``passing`` is a verification command that
actually exits 0. The agent cannot hand-promote a feature. This script is that
gate:

    state == "passing"  MUST imply  verification exits 0   (else: LIE)
    state != "passing"  but verification exits 0           (DRIFT: doc is stale)

A LIE is the false-confidence failure ("done" without evidence) that a stale
human doc allows and a primitive forbids. The gate exits non-zero on any LIE.

Usage:
    python tools/feature_gate.py                 # report only (no mutation)
    python tools/feature_gate.py --sync          # promote DRIFT -> passing (+evidence)
    python tools/feature_gate.py --only P1-3     # gate a single feature

Run from the repo root with the project's venv active.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEATURE_FILE = ROOT / "feature_list.json"

GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def run_verification(cmd: str) -> tuple[bool, str]:
    """Run a feature's verification command; return (passed, last_output_line)."""
    proc = subprocess.run(
        cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=600
    )
    out = (proc.stdout + proc.stderr).strip().splitlines()
    tail = out[-1] if out else ""
    return proc.returncode == 0, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true",
                    help="promote DRIFT (verified but mislabeled) to passing")
    ap.add_argument("--only", help="gate only the feature with this id")
    args = ap.parse_args()

    data = json.loads(FEATURE_FILE.read_text(encoding="utf-8"))
    features = data["features"]
    if args.only:
        features = [f for f in features if f["id"] == args.only]

    lies, drifts, verified, open_ = [], [], [], []
    print(f"{DIM}feature              state         verification{RST}")
    print("-" * 78)
    changed = False
    for f in features:
        passed, tail = run_verification(f["verification"])
        state = f["state"]
        is_passing = state == "passing"
        if is_passing and passed:
            verified.append(f); tag = f"{GREEN}OK  passing (verified){RST}"
        elif is_passing and not passed:
            lies.append(f); tag = f"{RED}LIE passing but verification FAILS{RST}"
        elif not is_passing and passed:
            drifts.append(f); tag = f"{YEL}DRIFT verified but marked {state}{RST}"
            if args.sync:
                f["state"] = "passing"
                f["evidence"] = f"{date.today().isoformat()} feature_gate: {tail}".strip()
                changed = True
        else:
            open_.append(f); tag = f"{DIM}open  {state} (verification fails — genuinely TODO){RST}"
        print(f"{f['id']:<20} {state:<13} {tag}")

    if args.sync and changed:
        FEATURE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")

    total = len(features)
    print("-" * 78)
    print(f"verified passing: {len(verified)}  |  DRIFT: {len(drifts)}  |  "
          f"LIE: {len(lies)}  |  open: {len(open_)}  |  total: {total}")
    non_passing = total - len(verified) - (len(drifts) if args.sync else 0)
    print(f"back-pressure (non-passing): {non_passing}"
          + ("  -> 0 = project complete" if non_passing == 0 else ""))

    if lies:
        print(f"\n{RED}GATE FAIL{RST} — {len(lies)} feature(s) claim 'passing' but "
              f"verification fails: {', '.join(f['id'] for f in lies)}")
        return 1
    if drifts and not args.sync:
        print(f"\n{YEL}DRIFT{RST} — {len(drifts)} feature(s) are verified but the doc "
              f"says otherwise: {', '.join(f['id'] for f in drifts)}. "
              f"Run with --sync to reconcile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
