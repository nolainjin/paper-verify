# Design: Web-chat + CLI accessibility update (2026-06-11)

Approved by: 진두찬 (2026-06-11 session). Branch: `feat/webchat-and-cli-accessibility` (base: main @ cb18481, after `harness/feature-list-pilot` ff-merge).

## Goal

Make paper-verify easy to use for **two audiences at once**:

1. **CLI users** — currently must clone + `pip install -e .`; README shows PyPI-style
   commands that 404 (`paper-verify` is not on PyPI).
2. **Web-chat users** (claude.ai / ChatGPT / Gemini web) — currently have **no path at
   all**; every documented entry point assumes a terminal.

## Approved decisions

- Web-chat = **both** a portable copy-paste prompt (any web chat) **and** a claude.ai
  skill package (hybrid: deterministic code for extract/score, model tools for
  fetch/judge).
- CLI = git-direct execution now (`uvx --from git+…`, pipx, clone); **PyPI prepared but
  not published** (trusted-publishing workflow ready; the one-time PyPI account link is
  a separate manual step).
- Base: `harness/feature-list-pilot` merged to main first (closes the carried P0);
  work happens on a new branch off main.
- `CLAUDE-HANDOFF.md`: migrate still-true content into `AGENTS.md`, then **delete**
  (stale 2026-06-04 snapshot contradicting reality in a public repo).

## Deliverables

| ID | Artifact | Path |
|---|---|---|
| D1 | Offline scoring entry point `--from-evidence` | `paperverify/offline.py`, `paperverify/cli.py` |
| D2 | Portable web-chat prompt (EN + KO) | `docs/webchat/webchat-prompt.md`, `docs/webchat/webchat-prompt.ko.md` |
| D3 | claude.ai skill (SKILL.md + bundled package) + zip builder | `integrations/skills/paper-verify-webchat/SKILL.md`, `tools/build_webchat_skill.py` |
| D4 | README restructure (quickstart-first, web-chat section, fixed install commands) | `README.md` |
| D5 | Korean guide | `README.ko.md` |
| D6 | CI + release workflows + release doc | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `docs/RELEASING.md` |
| D7 | Handoff migration + deletion | `AGENTS.md` (+), `CLAUDE-HANDOFF.md` (−) |
| D8 | Tests + feature registration | `tests/test_offline.py`, `tests/test_webchat_assets.py`, `feature_list.json` |

### D1 — `--from-evidence` (keystone)

A web chat (or any external agent) does fetch + judging with its own tools, then hands
paper-verify an **evidence JSON**; paper-verify converts it to the existing dataclasses
and reuses `score_citation` + `Report` + `render_markdown` / `render_json` — **no new
scoring logic**, so web-chat results use the exact same rubric as the CLI.

- CLI shape: `paper-verify --from-evidence evidence.json [--json] [--out DIR]`; the
  positional file argument becomes optional in this mode (precedent: `--list-profiles`).
- Evidence JSON contract (field names mirror `render_json` output / `models.py`):

```json
{
  "source_file": "paper.md",
  "level": "L2",
  "citations": [
    {
      "citation": {"type": "url", "ref": "https://…", "context": "~100 chars of claim", "line": 12},
      "fetched": {"status": 200, "title": "…", "abstract": "…", "url_final": "https://…",
                   "authors": ["…"], "year": 2024, "source": "http",
                   "soft_404_suspect": false, "error": null},
      "judgements": [{"judge": "webchat", "verdict": "Match", "reason": "one line"}]
    }
  ]
}
```

- Tolerant parsing: missing optional fields get neutral defaults; an unknown `verdict`
  or missing required field (`citation.ref`, `judgements[].verdict`) is a **clear error
  naming the bad citation index** (exit 2) — no silent guessing.
- Output is identical in shape to a normal run (`schema_version` 5 surface; `judges`
  taken from the evidence judgements).

### D2 — portable prompt

One self-contained markdown page per language. Protocol: extract citation table →
fetch each source with the chat's web tool → verdict with quoted evidence → score with
the embedded 100-point rubric → output report table + tier summary + Must Review list
(triage taxonomy identical to the Codex skill). Guardrails embedded: unreachable →
Inaccessible (never guess), no invented replacement citations, final-human-review
notice. States its limits (no Wayback fallback, soft-404 judgment is the model's).

### D3 — claude.ai skill

- Zip layout: `paper-verify-webchat/{SKILL.md, paperverify/**}` — the stdlib-only
  package itself is the bundle (zero source duplication); sandbox runs
  `python -m paperverify …` from the skill directory.
- SKILL.md workflow: ① run extraction script on the user's document ② fetch + judge
  each citation with Claude's web tools ③ assemble `evidence.json` ④ run
  `python -m paperverify --from-evidence evidence.json --json` ⑤ present report +
  triage shortlist. Fallback path when code execution is unavailable: follow the
  portable-prompt procedure inline.
- `tools/build_webchat_skill.py` builds the zip deterministically (also attached to
  GitHub Releases by D6).

### D4/D5 — README

- Quickstart first: ① `uvx --from git+https://github.com/nolainjin/paper-verify paper-verify doc.md`
  ② `pipx install git+…` ③ clone + `pip install -e .`.
- Every PyPI-style command replaced with a working form
  (`pip install "paper-verify[anthropic] @ git+…"` or clone + extras).
- New "💬 Use from a web chat (no terminal)" section linking D2 + D3.
- `README.ko.md`: focused ~150-line Korean guide (quickstart, levels, judges, web-chat
  paths, rubric, limits). README's bottom Korean summary shrinks to 2 lines + link.

### D6 — workflows

- `ci.yml`: pytest on push/PR (suite is network-free).
- `release.yml`: on tag `v*` → build sdist/wheel → publish via PyPI **trusted
  publishing** (`pypa/gh-action-pypi-publish`, `id-token: write`, environment `pypi`)
  → build + attach web-chat skill zip as a release asset.
- `docs/RELEASING.md`: one-time PyPI trusted-publisher setup, version bump, tag steps.
  Until the PyPI link is made, the publish job simply fails at auth — documented, not
  hidden.

### D7 — handoff migration

Still-true content in `CLAUDE-HANDOFF.md` (venv recreation command, schema-version
bump rule, no-provider-branches constraint) moves into `AGENTS.md` where not already
present; the file is then deleted. Everything else in it is stale (predates the public
repo) and is dropped deliberately.

### D8 — verification

- `tests/test_offline.py`: evidence JSON → report round-trip (verdict mapping, scores,
  tiers, error cases: unknown verdict, missing ref, empty citations).
- `tests/test_webchat_assets.py`: rubric anchor numbers in D2 prompts + D3 SKILL.md
  match `score.py` constants (claim-match 50/25/15/0/10, URL 20, author/year 20/10,
  cross-check 10, tier bounds 90/70/50) — drift guard for the tripled rubric text.
- `feature_list.json`: 3 new features (offline evidence path / webchat assets+drift
  guard / skill zip builder), each with a real verification command;
  `python tools/feature_gate.py --sync` promotes them.

## Acceptance criteria

1. Full pytest suite GREEN (existing + new).
2. `tools/feature_gate.py` exits 0 with the 3 new features `passing`.
3. `uvx --from git+https://github.com/nolainjin/paper-verify@feat/webchat-and-cli-accessibility paper-verify examples/sample.md --level L1` runs end-to-end on this machine (one real measurement; branch ref until merge).
4. Skill zip builds; from inside the zip directory `python -m paperverify --from-evidence <sample> --json` works.
5. README contains zero commands that 404 against PyPI.
6. `CLAUDE-HANDOFF.md` deleted; migrated lines present in `AGENTS.md`.
7. Workflow YAMLs verified by `actionlint` if available; otherwise by the real parse on push — `ci.yml` executes on the feature-branch push itself, which is the live check. Release flow documented as inactive until the PyPI link.

## Out of scope

- Actually publishing to PyPI (needs the user's PyPI account; prepared only).
- Hosted web app / GitHub Pages landing.
- The personal Korean 품앗이 skill in `memory/skills/paper-verify` (separate private
  harness; unchanged).
- New verification features (L4, new judges, etc.).

## Risks / notes

- claude.ai sandbox behavior (code execution availability, network limits) varies by
  plan; SKILL.md therefore carries the prompt-only fallback, and the portable prompt is
  the universal floor.
- Rubric text now exists in code + README + prompts; `test_webchat_assets.py` is the
  drift tripwire.
- `uvx --from git+…` resolves the package build from `pyproject.toml`; the stdlib-only
  core keeps that fast.
