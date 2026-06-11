# AGENTS.md — paper-verify

Short entrypoint for coding agents. Detail lives in linked files (progressive disclosure).

## What this is
A citation-verification tool (extract → fetch → judge → score → report). Pure-Python,
local-first. See `README.md` for the product; `pyproject.toml` for deps.

## Feature list is the source of truth (harness primitive)
`feature_list.json` is the single record of **what must work and whether it does** — not a
human note. Each feature = `(behavior, verification command, state, evidence)`.

Rules (do not bypass):
- **One feature `active` at a time.** Pick the next `not-started` whose `dependencies` are `passing`.
- **A feature reaches `passing` ONLY via its verification command exiting 0.** Never hand-edit
  `state` to `passing`. The gate controls the transition:
  ```sh
  source .venv/bin/activate
  python tools/feature_gate.py            # report drift / LIEs (state=passing must verify)
  python tools/feature_gate.py --sync     # promote verified features to passing (+evidence)
  ```
- **`state: passing` with a failing verification is a GATE FAIL (exit 1)** — the false-"done"
  guard. Wire `feature_gate.py` into pre-commit / CI to make it un-bypassable.
- Back-pressure = count of non-passing features. 0 = done.

## Verify before claiming done
- Full suite: `python -m pytest -q` (must stay green).
- The feature gate is the per-feature gate; the suite is the global gate.

## Dev setup
- Recreate the venv if missing: `python -m venv .venv && .venv/bin/pip install -e '.[dev]'`
- Full suite: `.venv/bin/python -m pytest -q`

## Provider neutrality (hard constraints)
- Do not rewrite the verifier core for one provider; no permanent provider
  branches — provider differences live in `paperverify/harness/` and
  `docs/providers/`.
- Keep tests network-free (monkeypatch fetch/judges) unless clearly marked
  as integration.
- Generated `*_report.md` / `*_claims.jsonl` stay out of commits
  (`.gitignore` covers them).

## Conventions
- SSoT: schema version in `paperverify/report.py` (`SCHEMA_VERSION`); IPC/shape changes bump it.
- Backlog/audit: `docs/harness/2026-06-04_paper-verify-audit/` (note: a static doc — it drifts;
  `feature_list.json` + the gate are authoritative for *current* state).
