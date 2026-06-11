# Web-chat + CLI Accessibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give paper-verify two first-class entry paths — zero-install CLI (`uvx` from git, PyPI-ready) and web-chat (portable prompt + claude.ai hybrid skill scoring through the real rubric via a new `--from-evidence` entry point).

**Architecture:** External agents (web chats) do fetch+judging with their own tools and hand paper-verify an *evidence JSON*; a new `paperverify/offline.py` converts it to the existing dataclasses and reuses `score_citation` + `Report` + renderers — no new scoring logic. Docs/prompts carry a copy of the rubric, guarded against drift by tests that compare them to `score.py` constants.

**Tech Stack:** Python ≥3.10 stdlib only (core), pytest, GitHub Actions (PyPI trusted publishing), zipfile for the skill bundle.

**Spec:** `docs/harness/2026-06-11_webchat-cli-accessibility-design.md` (D1–D8).
**Branch:** `feat/webchat-and-cli-accessibility` (already created; spec committed).
**Worktree note:** this repo is standalone and dedicated to this work; executing inline on the feature branch is acceptable (isolation via branch). All commands run from the repo root with `.venv` active: `source .venv/bin/activate` (Python 3.13.2 venv exists).

**Verification baseline before starting:** `python -m pytest -q` → expect **all green** (335 tests as of 2026-06-09). If red, stop and report.

---

### Task 1: Offline evidence scoring module (D1 core) — TDD

**Files:**
- Create: `tests/test_offline.py`
- Create: `paperverify/offline.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_offline.py`:

```python
"""Offline evidence scoring (--from-evidence) — no network, no API keys."""

import json
from pathlib import Path

import pytest

from paperverify import cli
from paperverify.offline import EvidenceError, report_from_evidence
from paperverify.report import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


def _evidence(**over):
    data = {
        "source_file": "demo.md",
        "level": "L2",
        "citations": [
            {
                "citation": {
                    "type": "URL",
                    "ref": "https://example.org/a",
                    "context": "Smith (2020) shows X",
                    "line": 3,
                },
                "fetched": {
                    "status": 200,
                    "title": "X study",
                    "abstract": "Smith 2020 shows X.",
                    "url_final": "https://example.org/a",
                    "source": "http",
                },
                "judgements": [
                    {"judge": "webchat:claude", "verdict": "Match", "reason": "explicitly supported"}
                ],
            },
            {
                "citation": {
                    "type": "DOI",
                    "ref": "10.1000/xyz",
                    "context": "claims Y rose 40%",
                    "line": 9,
                },
                "fetched": {
                    "status": 200,
                    "title": "Y paper",
                    "abstract": "Y fell.",
                    "source": "crossref",
                    "year": 2021,
                },
                "judgements": [
                    {"judge": "webchat:claude", "verdict": "Mismatch", "reason": "contradicted"}
                ],
            },
        ],
    }
    data.update(over)
    return data


def test_happy_path_scores_with_standard_rubric():
    report = report_from_evidence(_evidence())
    assert report.level == "L2"
    assert report.source_file == "demo.md"
    assert len(report.scored) == 2
    match, mismatch = report.scored
    assert match.breakdown["claim_match"] == 50
    assert mismatch.breakdown["claim_match"] == 0
    assert match.breakdown["url_accessible"] == 20
    # single judge -> no cross-check credit, identical to the CLI pipeline
    assert match.breakdown["cross_check"] == 0
    assert report.judges == ["webchat:claude"]


def test_ids_are_assigned_when_missing():
    report = report_from_evidence(_evidence())
    assert [sc.citation.id for sc in report.scored] == [1, 2]


def test_unknown_verdict_names_citation_index():
    bad = _evidence()
    bad["citations"][1]["judgements"][0]["verdict"] = "Confirmed"
    with pytest.raises(EvidenceError, match=r"citations\[1\].judgements\[0\]"):
        report_from_evidence(bad)


def test_missing_ref_names_citation_index():
    bad = _evidence()
    del bad["citations"][0]["citation"]["ref"]
    with pytest.raises(EvidenceError, match=r"citations\[0\]"):
        report_from_evidence(bad)


def test_citations_must_be_a_list():
    with pytest.raises(EvidenceError, match="'citations' must be a list"):
        report_from_evidence({"citations": {}})


def test_level_defaults_to_l2_and_validates():
    report = report_from_evidence({"citations": []})
    assert report.level == "L2"
    with pytest.raises(EvidenceError, match="unknown level"):
        report_from_evidence({"level": "L9", "citations": []})


def test_fetched_may_be_null():
    ev = _evidence()
    ev["citations"][0]["fetched"] = None
    report = report_from_evidence(ev)
    assert report.scored[0].breakdown["url_accessible"] == 0


def test_bad_year_type_is_a_clear_error():
    ev = _evidence()
    ev["citations"][1]["fetched"]["year"] = "n/a"
    with pytest.raises(EvidenceError, match=r"citations\[1\].fetched"):
        report_from_evidence(ev)
```

- [ ] **Step 1.2: Run to verify failure**

Run: `python -m pytest tests/test_offline.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'paperverify.offline'`

- [ ] **Step 1.3: Implement `paperverify/offline.py`**

```python
"""Score externally-gathered evidence with the standard rubric.

An external agent (a web chat, another harness) does fetch + judging with its
own tools, writes an *evidence JSON* (see ``examples/evidence-sample.json``),
and this module converts it to the shared dataclasses and reuses
``score_citation`` + ``Report`` — so external results get exactly the same
100-point rubric as the CLI pipeline. No new scoring logic lives here.
"""

from __future__ import annotations

from .models import Citation, Fetched, Judgement, Report, Verdict
from .score import score_citation

VALID_LEVELS = {"L1", "L2", "L3"}

_VERDICT_NAMES = " | ".join(v.value for v in Verdict)


class EvidenceError(ValueError):
    """Evidence JSON is malformed; the message names the offending citation index."""


def report_from_evidence(data: dict) -> Report:
    """Convert an evidence dict to a scored :class:`Report`.

    Tolerant on optional fields (missing ids are assigned 1..N, missing
    ``fetched`` fields take the dataclass defaults); strict with clear,
    index-named errors on anything that would otherwise be guessed.
    """
    if not isinstance(data, dict):
        raise EvidenceError("evidence root must be a JSON object")
    level = str(data.get("level", "L2")).upper()
    if level not in VALID_LEVELS:
        raise EvidenceError(f"unknown level: {level!r} (expected L1 | L2 | L3)")
    items = data.get("citations")
    if not isinstance(items, list):
        raise EvidenceError("'citations' must be a list")

    scored = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise EvidenceError(f"citations[{i}] must be an object")

        cd = item.get("citation")
        if not isinstance(cd, dict):
            raise EvidenceError(f"citations[{i}].citation must be an object")
        cd = dict(cd)
        cd.setdefault("id", i + 1)
        cd.setdefault("type", "URL")
        if not cd.get("ref"):
            raise EvidenceError(f"citations[{i}].citation.ref is required")
        try:
            citation = Citation.from_dict(cd)
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError(f"citations[{i}].citation invalid: {exc}") from None

        fd = item.get("fetched")
        fetched = None
        if fd is not None:
            if not isinstance(fd, dict):
                raise EvidenceError(f"citations[{i}].fetched must be an object or null")
            fd = dict(fd)
            fd.setdefault("id", citation.id)
            try:
                fetched = Fetched.from_dict(fd)
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceError(f"citations[{i}].fetched invalid: {exc}") from None

        judgements = []
        for k, jd in enumerate(item.get("judgements") or []):
            if not isinstance(jd, dict):
                raise EvidenceError(f"citations[{i}].judgements[{k}] must be an object")
            raw = jd.get("verdict")
            if raw is None:
                raise EvidenceError(f"citations[{i}].judgements[{k}].verdict is required")
            try:
                verdict = Verdict(str(raw))
            except ValueError:
                raise EvidenceError(
                    f"citations[{i}].judgements[{k}].verdict {raw!r} unknown "
                    f"(expected {_VERDICT_NAMES})"
                ) from None
            judgements.append(
                Judgement(
                    judge=str(jd.get("judge", "external")),
                    verdict=verdict,
                    reason=str(jd.get("reason", "")),
                )
            )

        scored.append(score_citation(citation, fetched, judgements, level=level))

    judges = sorted({j.judge for sc in scored for j in sc.judgements})
    return Report(
        source_file=str(data.get("source_file") or "<evidence>"),
        level=level,
        scored=scored,
        judges=judges,
        profile=data.get("profile"),
    )
```

- [ ] **Step 1.4: Run module tests** (CLI tests in the file still fail — that is Task 2)

Run: `python -m pytest tests/test_offline.py -q -k "not cli"`
Expected: PASS (9 tests)

- [ ] **Step 1.5: Commit**

```bash
git add paperverify/offline.py tests/test_offline.py
git commit -m "feat(offline): score external fetch/judge evidence with the standard rubric"
```

---

### Task 2: CLI `--from-evidence` + `--extract-only` (D1 surface) — TDD

**Files:**
- Modify: `paperverify/cli.py`
- Modify: `tests/test_offline.py` (append CLI tests)

- [ ] **Step 2.1: Append failing CLI tests to `tests/test_offline.py`**

```python
def test_cli_from_evidence_json_stdout(tmp_path, capsys):
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(_evidence()), encoding="utf-8")
    rc = cli.main(["--from-evidence", str(p), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["judges"] == ["webchat:claude"]
    assert payload["source_file"] == "demo.md"


def test_cli_from_evidence_rejects_extra_file(tmp_path):
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(_evidence()), encoding="utf-8")
    assert cli.main([str(p), "--from-evidence", str(p)]) == 2


def test_cli_from_evidence_bad_json(tmp_path):
    p = tmp_path / "evidence.json"
    p.write_text("{nope", encoding="utf-8")
    assert cli.main(["--from-evidence", str(p), "--json"]) == 2


def test_cli_from_evidence_bad_evidence_exits_2(tmp_path):
    bad = _evidence()
    bad["citations"][0]["judgements"][0]["verdict"] = "Confirmed"
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert cli.main(["--from-evidence", str(p), "--json"]) == 2


def test_cli_from_evidence_missing_file(tmp_path):
    assert cli.main(["--from-evidence", str(tmp_path / "nope.json")]) == 2


def test_cli_from_evidence_writes_report_files(tmp_path, capsys):
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(_evidence()), encoding="utf-8")
    rc = cli.main(["--from-evidence", str(p), "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "evidence_report.md").is_file()
    assert (tmp_path / "evidence_claims.jsonl").is_file()


def test_cli_extract_only(tmp_path, capsys):
    doc = tmp_path / "doc.md"
    doc.write_text("see https://example.org/a and DOI 10.1000/xyz", encoding="utf-8")
    rc = cli.main([str(doc), "--extract-only"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    types = [c["type"] for c in payload["citations"]]
    assert types == ["URL", "DOI"]
    assert payload["citations"][0]["id"] == 1
```

- [ ] **Step 2.2: Run to verify failure**

Run: `python -m pytest tests/test_offline.py -q`
Expected: FAIL — `unrecognized arguments: --from-evidence` (argparse SystemExit) for the new tests; the Task 1 tests stay green.

- [ ] **Step 2.3: Modify `paperverify/cli.py`**

(a) Add imports — extend the existing import block:

```python
from .extract import extract
from .offline import report_from_evidence
```

(`from .extract import extract` already exists; add only the `offline` line under it.)

(b) In `_build_parser()`, after the `--list-profiles` argument, add:

```python
    p.add_argument(
        "--extract-only",
        action="store_true",
        help="extract citations and print them as JSON to stdout — no network, "
        "no LLM (agent surface; pairs with --from-evidence)",
    )
    p.add_argument(
        "--from-evidence",
        default=None,
        metavar="FILE",
        help="skip extract/fetch/judge and score an externally-gathered evidence "
        "JSON file with the standard rubric (see docs/webchat/). Omit the "
        "positional file argument in this mode.",
    )
```

(c) Extract the output tail of `main()` (everything from `out = args.out` through the final `return 0`, currently after the `run_pipeline` call) into a module-level helper, replacing `src.stem` with the `base` parameter:

```python
def _emit(report: Report, args, *, base: str, as_json: bool, human) -> int:
    """Write files / JSON / human summary for a finished report (shared tail)."""
    out = args.out
    if out is None and not as_json:
        out = "."
    if out is not None:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / f"{base}_report.md"
        claims_path = out_dir / f"{base}_claims.jsonl"
        report_path.write_text(render_markdown(report), encoding="utf-8")
        claims_path.write_text(
            report.to_jsonl() + ("\n" if report.scored else ""), encoding="utf-8"
        )
    else:
        report_path = claims_path = None

    if as_json:
        print(json.dumps(render_json(report), ensure_ascii=False))

    dist = report.tier_distribution()
    summary = " ".join(
        f"{t.symbol}{t.value}:{dist.get(t, 0)}" for t in [Tier.A, Tier.B, Tier.C, Tier.F]
    )
    print(
        f"Overall: {report.overall_score}/100 "
        f"{report.overall_tier.symbol} {report.overall_tier.value}  [{summary}]",
        file=human,
    )
    if report.has_failure:
        print("⚠️  Document contains tier-F citations — see report.", file=human)
    if report_path is not None:
        print(f"Report:  {report_path}", file=human)
        print(f"Claims:  {claims_path}", file=human)
    return 0
```

(d) In `main()`, right after the `--list-profiles` early-return block (the `as_json` / `human` lines already follow it — keep them), insert the evidence branch:

```python
    # --from-evidence: score externally-gathered evidence; no positional file.
    if args.from_evidence:
        if args.file:
            print(
                "error: pass either a file or --from-evidence, not both",
                file=sys.stderr,
            )
            return 2
        ev_path = Path(args.from_evidence)
        if not ev_path.is_file():
            print(f"error: file not found: {ev_path}", file=sys.stderr)
            return 2
        try:
            data = json.loads(ev_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: invalid JSON in {ev_path}: {exc}", file=sys.stderr)
            return 2
        try:
            report = report_from_evidence(data)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _emit(report, args, base=ev_path.stem, as_json=as_json, human=human)
```

(e) In `main()`, right after the file is read into `text` (after the `UnicodeDecodeError` fallback), insert:

```python
    if args.extract_only:
        cites = extract(text)
        print(json.dumps({"citations": [c.to_dict() for c in cites]}, ensure_ascii=False))
        return 0
```

(f) Replace the old output tail of `main()` (now duplicated by `_emit`) with:

```python
    return _emit(report, args, base=src.stem, as_json=as_json, human=human)
```

- [ ] **Step 2.4: Run the full suite** (refactor touched the shared tail — everything must stay green)

Run: `python -m pytest -q`
Expected: PASS, 0 failures (count grows by the new tests)

- [ ] **Step 2.5: Commit**

```bash
git add paperverify/cli.py tests/test_offline.py
git commit -m "feat(cli): --from-evidence offline scoring + --extract-only agent surface"
```

---

### Task 3: Evidence sample file (docs/tests/zip shared fixture)

**Files:**
- Create: `examples/evidence-sample.json`
- Modify: `tests/test_offline.py` (append round-trip test)

- [ ] **Step 3.1: Create `examples/evidence-sample.json`**

```json
{
  "source_file": "demo.md",
  "level": "L2",
  "citations": [
    {
      "citation": {
        "type": "URL",
        "ref": "https://example.org/a",
        "context": "Smith (2020) shows X",
        "line": 3
      },
      "fetched": {
        "status": 200,
        "title": "X study",
        "abstract": "Smith 2020 shows X.",
        "url_final": "https://example.org/a",
        "source": "http"
      },
      "judgements": [
        {
          "judge": "webchat:claude",
          "verdict": "Match",
          "reason": "explicitly supported"
        }
      ]
    },
    {
      "citation": {
        "type": "DOI",
        "ref": "10.1000/xyz",
        "context": "claims Y rose 40%",
        "line": 9
      },
      "fetched": {
        "status": 200,
        "title": "Y paper",
        "abstract": "Y fell.",
        "source": "crossref",
        "year": 2021
      },
      "judgements": [
        {
          "judge": "webchat:claude",
          "verdict": "Mismatch",
          "reason": "contradicted"
        }
      ]
    }
  ]
}
```

- [ ] **Step 3.2: Append the round-trip test to `tests/test_offline.py`**

```python
def test_shipped_sample_evidence_round_trips():
    sample = ROOT / "examples" / "evidence-sample.json"
    report = report_from_evidence(json.loads(sample.read_text(encoding="utf-8")))
    assert len(report.scored) == 2
    assert report.has_failure  # the Mismatch citation lands in tier F by design
```

- [ ] **Step 3.3: Run** `python -m pytest tests/test_offline.py -q` — Expected: PASS

- [ ] **Step 3.4: Commit**

```bash
git add examples/evidence-sample.json tests/test_offline.py
git commit -m "docs(examples): shipped evidence-sample.json + round-trip guard"
```

---

### Task 4: Rubric drift-guard tests (RED first — files don't exist yet)

**Files:**
- Create: `tests/test_webchat_assets.py`

- [ ] **Step 4.1: Create `tests/test_webchat_assets.py`**

```python
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
```

- [ ] **Step 4.2: Run to verify failure**

Run: `python -m pytest tests/test_webchat_assets.py -q`
Expected: FAIL — all assets missing

- [ ] **Step 4.3: Commit the red tests** (they go green over Tasks 5–6)

```bash
git add tests/test_webchat_assets.py
git commit -m "test(webchat): rubric drift tripwire for prompts + skill (red until assets land)"
```

---

### Task 5: Portable web-chat prompts (EN + KO)

**Files:**
- Create: `docs/webchat/webchat-prompt.md`
- Create: `docs/webchat/webchat-prompt.ko.md`

Both files embed the **same English rubric tables verbatim** (that is what the drift test pins; KO prose surrounds the same tables).

- [ ] **Step 5.1: Create `docs/webchat/webchat-prompt.md`**

````markdown
# paper-verify — web-chat citation check (portable prompt)

Copy this **entire page** into any AI web chat (Claude / ChatGPT / Gemini) that can
browse the web, then paste or attach your document. The assistant will fact-check
your citations with the same 100-point rubric as the
[paper-verify CLI](https://github.com/nolainjin/paper-verify).

> Single-chat limitation: there is only **one** judge (this assistant), so the
> Cross-check slot scores 0 and the maximum is **90** — the CLI behaves the same
> with one judge. For multi-judge cross-checking, use the CLI.

---

You are a citation fact-checker. Follow this protocol exactly.

## Step 1 — Extract

List every reference in the document: URLs, DOIs (`10.xxxx/…`), `PMC…`, `PMID…`,
`arXiv:…`. For each, record: `id` (1..N), `type`, `ref`, ~100 characters of
surrounding text (the **claim** being made), and the line/paragraph it appears in.
Deduplicate by (type, ref); a bare DOI duplicates its own `doi.org/…` URL.
Show the table before continuing.

## Step 2 — Fetch

For each reference, open it with your web tool (search for the title if the URL
fails). Record: HTTP reachability, page title, the abstract or the passage
relevant to the claim, final URL, authors and year when visible. If a page is
clearly an error/placeholder despite loading, mark it `soft-404 suspect`.
If you cannot access the source at all, mark it **Inaccessible — do not guess.**

## Step 3 — Judge

For each citation, compare the **claim** against what the source actually says
and give a verdict with a one-line reason **quoting the source**:

| Verdict | Meaning |
|---|---|
| ✅ Match | claim is explicitly supported by the source |
| ⚠️ Partial | partially supported; numbers / year / nuance differ |
| ❌ Mismatch | absent from, or contradicted by, the source |
| ❓ Uncertain | source insufficient to decide — flag for human review |
| ⚫ Inaccessible | paywall / 404 / timeout — could not verify |

Prefer **Uncertain** over guessing.

## Step 4 — Score (100-point rubric)

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | source opened successfully (soft-404 suspect: 0) |
| Author / year match | 20 / 10 / 0 | author **and** year align = 20; only one = 10; neither = 0; no metadata to compare = neutral 10 |
| Claim match | 50 | Match = 50 · Partial = 25 · Uncertain = 15 · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | requires 2+ independent judges — in a single chat this is always 0 |

Tier per citation (and document average):

| Tier | Score | Meaning |
|---|---|---|
| 🟢 A | 90–100 | citable in a thesis / formal report |
| 🟡 B | 70–89 | fine for a lecture / blog, minor fixes |
| 🟠 C | 50–69 | must be re-checked |
| 🔴 F | 0–49 | do not cite — replace the source |

## Step 5 — Report

Output, in this order:

1. **Overall**: average score, tier, tier distribution (A/B/C/F counts). If any
   citation is tier F, start with: `⚠️ DOCUMENT-LEVEL WARNING — tier-F citation(s) present.`
2. A table: id | ref | verdict | score | tier | one-line reason.
3. **Must Review** — every citation that is tier F, Mismatch, Uncertain,
   Inaccessible, or soft-404 suspect, with what to check by hand.
4. **Probably Safe** — tier A/B with Match/Partial and no access problems.

## Guardrails

- Never claim a citation is supported just because the link works.
- Never invent or substitute citations; if asked for replacements, label them
  clearly as **suggestions to verify**, not verified sources.
- This is a triage pass, not a substitute for expert review — say so at the end.
- State plainly when a source was checked via its abstract/metadata only.

---

*Generated from [paper-verify](https://github.com/nolainjin/paper-verify); rubric
mirrors `paperverify/score.py` and is drift-tested in CI
(`tests/test_webchat_assets.py`).*
````

- [ ] **Step 5.2: Create `docs/webchat/webchat-prompt.ko.md`**

Same protocol in Korean; the two rubric tables and the verdict table stay in
English **verbatim** (drift anchors). Full content:

````markdown
# paper-verify — 웹챗 인용 검증 (복붙 프롬프트)

이 **페이지 전체**를 웹 검색이 가능한 AI 웹챗(Claude / ChatGPT / Gemini)에 붙여넣고,
검증할 문서를 이어서 붙여넣거나 첨부하세요. 어시스턴트가
[paper-verify CLI](https://github.com/nolainjin/paper-verify)와 동일한 100점
루브릭으로 인용을 검증합니다.

> 단일 챗 한계: 심판이 어시스턴트 **하나**뿐이라 Cross-check 항목은 0점, 만점은
> **90점**입니다 — CLI도 심판 1명일 때 동일합니다. 다중 심판 교차검증은 CLI를 쓰세요.

---

당신은 인용 사실검증가입니다. 아래 프로토콜을 그대로 따르세요.

## 1단계 — 추출

문서의 모든 출처를 나열: URL, DOI(`10.xxxx/…`), `PMC…`, `PMID…`, `arXiv:…`.
각 항목에 `id`(1..N), `type`, `ref`, 주변 ~100자(**주장** 맥락), 등장 위치(행/단락)를
기록. (type, ref)로 중복 제거 — 동일 DOI의 `doi.org/…` URL은 같은 출처입니다.
표를 먼저 보여준 뒤 진행하세요.

## 2단계 — 확인(fetch)

각 출처를 웹 도구로 엽니다(URL 실패 시 제목으로 검색). 기록: 접속 가능 여부,
페이지 제목, 주장과 관련된 초록/구절, 최종 URL, 보이는 경우 저자·연도.
로딩은 되지만 에러/placeholder 페이지면 `soft-404 의심` 표기.
출처에 전혀 접근할 수 없으면 **Inaccessible — 추측 금지.**

## 3단계 — 판정

각 인용의 **주장**과 출처의 실제 내용을 비교해, **출처를 인용한 한 줄 근거**와
함께 판정:

| Verdict | Meaning |
|---|---|
| ✅ Match | claim is explicitly supported by the source |
| ⚠️ Partial | partially supported; numbers / year / nuance differ |
| ❌ Mismatch | absent from, or contradicted by, the source |
| ❓ Uncertain | source insufficient to decide — flag for human review |
| ⚫ Inaccessible | paywall / 404 / timeout — could not verify |

추측하느니 **Uncertain**을 택하세요.

## 4단계 — 채점 (100점 루브릭)

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | source opened successfully (soft-404 suspect: 0) |
| Author / year match | 20 / 10 / 0 | author **and** year align = 20; only one = 10; neither = 0; no metadata to compare = neutral 10 |
| Claim match | 50 | Match = 50 · Partial = 25 · Uncertain = 15 · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | requires 2+ independent judges — in a single chat this is always 0 |

인용별(및 문서 평균) 티어:

| Tier | Score | Meaning |
|---|---|---|
| 🟢 A | 90–100 | citable in a thesis / formal report |
| 🟡 B | 70–89 | fine for a lecture / blog, minor fixes |
| 🟠 C | 50–69 | must be re-checked |
| 🔴 F | 0–49 | do not cite — replace the source |

## 5단계 — 보고

다음 순서로 출력:

1. **종합**: 평균 점수, 티어, 티어 분포(A/B/C/F 개수). F 인용이 하나라도 있으면
   맨 앞에 `⚠️ 문서 경고 — tier-F 인용 존재.`
2. 표: id | ref | verdict | score | tier | 한 줄 근거.
3. **Must Review** — tier F·Mismatch·Uncertain·Inaccessible·soft-404 의심 전부
   + 사람이 직접 확인할 포인트.
4. **Probably Safe** — 접근 문제 없는 Match/Partial의 A/B 티어.

## 가드레일

- 링크가 열린다는 이유만으로 "주장이 지지된다"고 말하지 않는다.
- 인용을 지어내거나 임의 대체하지 않는다. 대체 출처를 요청받으면 **검증 필요한
  제안**임을 명시한다.
- 이것은 1차 triage이며 전문가 최종 검토를 대체하지 않는다 — 말미에 고지한다.
- 초록/메타데이터만으로 확인한 출처는 그 사실을 명시한다.

---

*[paper-verify](https://github.com/nolainjin/paper-verify)에서 생성. 루브릭은
`paperverify/score.py`를 그대로 따르며 CI 드리프트 테스트
(`tests/test_webchat_assets.py`)로 고정됩니다.*
````

- [ ] **Step 5.3: Run drift tests — prompts now green, SKILL.md still red**

Run: `python -m pytest tests/test_webchat_assets.py -q`
Expected: prompt-file tests PASS; the `SKILL.md` asset tests still FAIL (Task 6)

- [ ] **Step 5.4: Commit**

```bash
git add docs/webchat/
git commit -m "docs(webchat): portable copy-paste verification prompt (EN+KO)"
```

---

### Task 6: claude.ai web-chat skill

**Files:**
- Create: `integrations/skills/paper-verify-webchat/SKILL.md`

- [ ] **Step 6.1: Create `integrations/skills/paper-verify-webchat/SKILL.md`**

````markdown
---
name: paper-verify-webchat
description: Use when the user asks to fact-check, verify, or score the citations/sources/references in a document from a chat (no terminal) — extracts citations with bundled code, fetches and judges each source with web tools, then scores through paper-verify's standard 100-point rubric via --from-evidence.
---

# Paper Verify (web chat)

Citation verification from a chat. Deterministic parts (extraction, rubric
scoring, report) run as **bundled code**; fetching and judging use **your web
tools**. The rubric is exactly the CLI's (`paperverify/score.py`).

## Requirements

- This skill folder bundles the stdlib-only `paperverify` package — no pip
  install needed. Run all commands **from this skill's directory** (the folder
  containing this SKILL.md), so `python -m paperverify` resolves.
- Code execution unavailable? Follow `fallback-prompt.md` (bundled) inline
  instead — same protocol, model-computed scores, clearly lower precision.

## Workflow

1. **Get the document.** Save the user's text/upload to a file, e.g.
   `/tmp/doc.md`.
2. **Extract (code):**

   ```bash
   python -m paperverify /tmp/doc.md --extract-only > /tmp/citations.json
   ```

   Show the user a short table (id, type, ref, line) and the count.
3. **Fetch + judge (web tools).** For each citation object: open `ref` with
   web fetch (search the title if it fails). Then build one evidence item:
   - `citation`: the object from step 2, **verbatim**.
   - `fetched`: `status` (HTTP-ish: 200 ok / 404 dead / 0 unreachable),
     `title`, `abstract` (the relevant passage, ≤1500 chars), `url_final`,
     `authors` (list, if visible), `year` (int or null), `source`: `"http"`
     (or `"none"` when unreachable — then also set `error`),
     `soft_404_suspect`: true for error/placeholder pages that return 200.
   - `judgements`: exactly one — `{"judge": "webchat:claude", "verdict":
     Match|Partial|Mismatch|Uncertain|Inaccessible, "reason": "one line quoting
     the source"}`. Unreachable source ⇒ `Inaccessible`; never guess
     (prefer `Uncertain`).
4. **Assemble** `/tmp/evidence.json`:

   ```json
   {"source_file": "doc.md", "level": "L2", "citations": [ …evidence items… ]}
   ```

   (Shape reference: bundled `examples/evidence-sample.json`.)
5. **Score + report (code):**

   ```bash
   python -m paperverify --from-evidence /tmp/evidence.json --json --out /tmp/pv
   ```

   Parse the JSON from stdout; `/tmp/pv/evidence_report.md` is the shareable
   report (offer it to the user).
6. **Present:** overall score/tier, tier distribution, then the triage lists:
   - **Must Review** — tier `F`, verdict `Mismatch`/`Uncertain`/`Inaccessible`,
     or `soft_404_suspect`.
   - **Review If Important** — tier `C`, or partial author/year match only.
   - **Probably Safe** — tier `A`/`B`, verdict Match/Partial, no access issues.

## Scoring (for transparency — computed by the code, not by you)

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | HTTP 2xx |
| Author / year match | 20 / 10 / 0 | both = 20; one = 10; neither = 0; no metadata = neutral 10 |
| Claim match | 50 | Match = 50 · Partial = 25 · Uncertain = 15 · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | needs 2+ judges; single web-chat judge ⇒ 0 (max 90) |

| Tier | Score |
|---|---|
| 🟢 A | 90–100 |
| 🟡 B | 70–89 |
| 🟠 C | 50–69 |
| 🔴 F | 0–49 |

## Guardrails

- Reachable ≠ supported; the verdict must come from comparing claim vs source.
- Do not invent or auto-substitute citations.
- Final expert review is still required for publication/thesis/legal/medical
  use — say so.
- If many citations (>30), confirm with the user before fetching all of them.
````

- [ ] **Step 6.2: Run drift tests — all green now**

Run: `python -m pytest tests/test_webchat_assets.py -q`
Expected: PASS (all)

- [ ] **Step 6.3: Commit**

```bash
git add integrations/skills/paper-verify-webchat/
git commit -m "feat(skill): claude.ai web-chat skill — hybrid code/web-tool workflow"
```

---

### Task 7: Skill zip builder

**Files:**
- Create: `tools/build_webchat_skill.py`

- [ ] **Step 7.1: Create `tools/build_webchat_skill.py`**

```python
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
```

- [ ] **Step 7.2: Run the builder check**

Run: `python tools/build_webchat_skill.py --check`
Expected: `OK: …/paper-verify-webchat-skill.zip (NN files)` , exit 0

- [ ] **Step 7.3: Smoke-test the bundle actually runs as a skill would use it**

```bash
cd "$(mktemp -d)" && python "$OLDPWD/tools/build_webchat_skill.py" --out . \
  && unzip -q paper-verify-webchat-skill.zip && cd paper-verify-webchat \
  && python -m paperverify --from-evidence examples/evidence-sample.json --json >/dev/null \
  && echo BUNDLE-OK && cd "$OLDPWD"
```

Expected: `BUNDLE-OK`

- [ ] **Step 7.4: Commit**

```bash
git add tools/build_webchat_skill.py
git commit -m "feat(tools): deterministic claude.ai skill zip builder (--check gate)"
```

---

### Task 8: README restructure (D4)

**Files:**
- Modify: `README.md`

- [ ] **Step 8.1: Add the Korean-guide link under the title.** After the line
`# paper-verify`, insert:

```markdown
> **한국어 가이드** → [README.ko.md](README.ko.md)
```

- [ ] **Step 8.2: Replace the `## Install` + `## Quickstart` sections** (current
lines: `## Install` through the end of the L2 judge example, just before
`## Verification levels`) with:

````markdown
## Quickstart

Run it **without installing anything** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uvx --from git+https://github.com/nolainjin/paper-verify paper-verify yourdoc.md --level L2
```

Runs with **no API keys** (keyword judge, low confidence). Output resembles:

```
paper-verify: 5 citations, level L2, judges: keyword
Overall: <score>/100 <tier>  [🟢A:<n> 🟡B:<n> 🟠C:<n> 🔴F:<n>]
⚠️  Document contains tier-F citations — see report.
Report:  /tmp/pv/yourdoc_report.md
Claims:  /tmp/pv/yourdoc_claims.jsonl
```

For a real fact-check, add an LLM judge:

```bash
export ANTHROPIC_API_KEY=sk-...
uvx --from "git+https://github.com/nolainjin/paper-verify" --with anthropic \
  paper-verify paper.md --level L2 --judge anthropic:claude-sonnet-4-6
```

## 💬 No terminal? Use it from a web chat

- **Any web chat** (Claude / ChatGPT / Gemini with browsing): copy-paste
  [`docs/webchat/webchat-prompt.md`](docs/webchat/webchat-prompt.md)
  (한국어: [`webchat-prompt.ko.md`](docs/webchat/webchat-prompt.ko.md)) — the
  model fetches your sources and scores them with this same 100-point rubric.
- **claude.ai (skill upload)**: upload the web-chat skill zip — extraction and
  scoring run as bundled code, fetching/judging use Claude's web tools, and the
  score comes from the real rubric via `--from-evidence`. Get the zip from
  [Releases](https://github.com/nolainjin/paper-verify/releases) or build it:
  `python tools/build_webchat_skill.py`.

## Install

> Not on PyPI yet — these install straight from GitHub. (The PyPI release
> workflow is ready; see `docs/RELEASING.md`.)

```bash
pipx install git+https://github.com/nolainjin/paper-verify          # isolated CLI
pip install "paper-verify @ git+https://github.com/nolainjin/paper-verify"  # core, stdlib only
pip install "paper-verify[anthropic] @ git+https://github.com/nolainjin/paper-verify"  # + Anthropic judge
# extras: [anthropic] [openai] [gemini] [mcp] [all] [dev]

# for development:
git clone https://github.com/nolainjin/paper-verify && cd paper-verify
pip install -e ".[dev]"
```

Requires Python ≥ 3.10. Without installing: `python -m paperverify yourdoc.md --level L2`.
````

- [ ] **Step 8.3: Fix the judges table install column.** In the `## Judges &
providers` table, replace the two cells
`` `pip install paper-verify[anthropic]`, `ANTHROPIC_API_KEY` `` →
`` extras `[anthropic]` (see Install), `ANTHROPIC_API_KEY` `` and the analogous
`[openai]` / `[gemini]` cells the same way.

- [ ] **Step 8.4: Fix the MCP section installs.** In `### (b) MCP server`:
replace the bash block `pip install paper-verify[mcp]` with:

```bash
pip install "paper-verify[mcp] @ git+https://github.com/nolainjin/paper-verify"
```

and in the closing paragraph replace `` `pip install paper-verify[mcp]` hint ``
with `` an install hint for the `[mcp]` extra ``.

- [ ] **Step 8.5: Add the bring-your-own-fetch agent surface.** After the
`### (b) MCP server` section (before `## Scoring rubric`), insert:

````markdown
### (c) `--from-evidence` — bring your own fetch/judge

If your agent (or a web chat) already fetched the sources and judged the
claims, hand paper-verify the evidence and let it apply the standard rubric —
identical scoring to a native run:

```bash
paper-verify yourdoc.md --extract-only > citations.json   # deterministic extraction
# …your agent fetches each citation and judges it, producing evidence.json…
paper-verify --from-evidence evidence.json --json --out out/
```

The evidence shape is documented by example in
[`examples/evidence-sample.json`](examples/evidence-sample.json): per citation,
the `citation` object from `--extract-only` verbatim, a `fetched` object
(`status`, `title`, `abstract`, `authors`, `year`, `source`,
`soft_404_suspect`, …), and one or more `judgements`
(`{"judge", "verdict", "reason"}` — verdicts: Match | Partial | Mismatch |
Uncertain | Inaccessible). Malformed evidence exits 2 with the offending
citation index named. This is the engine behind the web-chat skill.
````

- [ ] **Step 8.6: Shrink the Korean summary.** Replace the whole `## 한국어 요약`
section body with:

```markdown
## 한국어 요약

문서 안의 인용 출처(URL·DOI·PMC·PMID·arXiv)를 추출해 실제 원문과 대조하고 100점
루브릭으로 채점하는 도구입니다. **전체 한국어 가이드: [README.ko.md](README.ko.md)**
(터미널 없이 웹챗에서 쓰는 방법 포함).
```

- [ ] **Step 8.7: Verify no dead PyPI commands remain**

Run: `grep -n "pip install paper-verify\[" README.md; grep -n "pip install -e" README.md`
Expected: first grep → no matches (or only `@ git+` forms); second grep → only the development clone block.

- [ ] **Step 8.8: Commit**

```bash
git add README.md
git commit -m "docs(readme): quickstart-first (uvx/pipx/git), web-chat section, fix dead PyPI commands"
```

---

### Task 9: Korean guide `README.ko.md` (D5)

**Files:**
- Create: `README.ko.md`

- [ ] **Step 9.1: Create `README.ko.md`**

````markdown
# paper-verify — 한국어 가이드

**문서 속 인용을 사실검증합니다.** 마크다운/텍스트 문서에서 출처(URL · DOI ·
PMC · PMID · arXiv)를 전부 추출하고, 각 원문을 가져와 **인용된 주장이 실제로
지지되는지** LLM 심판으로 판정한 뒤, 투명한 **100점 루브릭**으로 채점해
보고서를 만듭니다. AI가 지어낸 인용·오인용·죽은 링크를 발행 전에 잡는
도구입니다. (대학원생·연구자·강사·블로거용)

전문 검토를 대체하지 않습니다 — 사람이 직접 봐야 할 출처를 **작게 추려주는
triage 도구**입니다.

## 빠른 시작 (터미널)

설치 없이 바로 ([uv](https://docs.astral.sh/uv/) 필요):

```bash
uvx --from git+https://github.com/nolainjin/paper-verify paper-verify 문서.md --level L2
```

설치해서 쓰기:

```bash
pipx install git+https://github.com/nolainjin/paper-verify
# 또는
pip install "paper-verify @ git+https://github.com/nolainjin/paper-verify"
# 개발용
git clone https://github.com/nolainjin/paper-verify && cd paper-verify && pip install -e ".[dev]"
```

API 키 없이도 동작합니다(`keyword` 심판 — 저신뢰 스모크 테스트). 실전 검증은
LLM 심판을 붙이세요:

```bash
export ANTHROPIC_API_KEY=sk-...
paper-verify 문서.md --level L2 --judge anthropic:claude-sonnet-4-6
```

> 아직 PyPI 미배포 — 위 명령은 GitHub에서 직접 받습니다. (`docs/RELEASING.md`)

## 💬 터미널 없이 — 웹챗에서

1. **아무 웹챗** (Claude / ChatGPT / Gemini, 웹검색 가능 모델):
   [`docs/webchat/webchat-prompt.ko.md`](docs/webchat/webchat-prompt.ko.md)를
   통째로 복사해 붙여넣고, 검증할 문서를 이어서 붙여넣으면 끝. 모델이 출처를
   직접 열어보고 같은 100점 루브릭으로 채점합니다.
2. **claude.ai 스킬 업로드**: 릴리즈의
   `paper-verify-webchat-skill.zip`을 claude.ai 스킬로 업로드하면 추출·채점은
   **코드가 결정적으로** 수행하고(fetch·판정은 Claude 웹도구), 점수는 CLI와
   동일한 루브릭(`--from-evidence`)에서 나옵니다.

## 검증 레벨

| 레벨 | 깊이 | 비용 |
|---|---|---|
| **L1** | 링크 생존만 (HTTP 2xx) | 네트워크만, LLM 0 |
| **L2** | 초록/제목 vs 주장 대조 (**기본**) | 인용당 LLM ~1회 |
| **L3** | 본문·수치 정합까지 | 인용당 LLM 여러 회 |

## 심판 (`--judge`, 반복 지정 = 교차검증)

| 스펙 | 요구사항 |
|---|---|
| `keyword` (기본) | 없음 — 항상 동작, 단 어휘 겹침만 보는 저신뢰 |
| `anthropic[:모델]` | extras `[anthropic]` + `ANTHROPIC_API_KEY` |
| `openai[:모델]` | extras `[openai]` + `OPENAI_API_KEY` |
| `gemini[:모델]` | extras `[gemini]` + `GEMINI_API_KEY` |
| `cli:claude` `cli:codex` `cli:gemini` | 해당 CLI가 PATH에 설치 |

심판 2개 이상이 갈리면 `--tiebreak <스펙>`이 갈린 인용만 재판정합니다.

## 100점 루브릭

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | HTTP 2xx |
| Author / year match | 20 / 10 / 0 | 저자+연도 모두 일치 20 / 하나만 10 / 불일치 0 / 비교할 메타데이터 없음 = 중립 10 |
| Claim match | 50 | Match = 50 · Partial = 25 · Uncertain = 15 · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | 심판 2+ 합의 시에만 |

| Tier | Score | 의미 |
|---|---|---|
| 🟢 A | 90–100 | 논문/공식 보고서 인용 가능 |
| 🟡 B | 70–89 | 강의/블로그 OK, 소수정 |
| 🟠 C | 50–69 | 재확인 필요 |
| 🔴 F | 0–49 | 인용 금지 — 출처 교체 |

F 인용이 하나라도 있으면 문서 전체에 ⚠️ 경고가 붙습니다.

## 에이전트에서 쓰기

- `--json`: 전체 결과를 JSON으로 stdout에 (사람용 요약은 stderr).
- `--extract-only`: 추출만 (네트워크·LLM 0).
- `--from-evidence evidence.json`: 외부에서 fetch·판정한 증거를 표준 루브릭으로
  채점 — 웹챗 스킬의 엔진. 예시: [`examples/evidence-sample.json`](examples/evidence-sample.json).
- MCP 서버: extras `[mcp]` 설치 후 `claude mcp add paper-verify -- paper-verify-mcp`.

## 한계

- LLM 심판도 초록을 오독할 수 있습니다 — 중요 인용은 교차검증(`--judge` 2개 +
  `--tiebreak`) 후 직접 확인하세요.
- 페이월 본문이 필요한 주장은 메타데이터만으로 C 티어에 머물 수 있습니다(정상).
- soft-404 감지는 휴리스틱이고, JS 위주 페이지는 본문 추출이 빈약할 수 있습니다.

MIT © 2026 진두찬 — 상세는 영문 [README.md](README.md).
````

- [ ] **Step 9.2: Commit**

```bash
git add README.ko.md
git commit -m "docs(ko): focused Korean guide (quickstart, web-chat paths, rubric)"
```

---

### Task 10: Handoff migration + deletion (D7 — user approved "이관 후 삭제")

**Files:**
- Modify: `AGENTS.md`
- Delete: `CLAUDE-HANDOFF.md` (untracked local file — verified gitignored, never public)
- Modify: `.gitignore`

- [ ] **Step 10.1: Append to `AGENTS.md`** (after the `## Verify before claiming done` section):

```markdown
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
```

- [ ] **Step 10.2: Delete the stale handoff + its ignore entry**

```bash
rm CLAUDE-HANDOFF.md
```

In `.gitignore`, remove the two lines:

```
# Internal handoff — not for publication
CLAUDE-HANDOFF.md
```

- [ ] **Step 10.3: Commit**

```bash
git add AGENTS.md .gitignore
git commit -m "chore(agents): migrate live handoff notes into AGENTS.md, drop stale CLAUDE-HANDOFF"
```

---

### Task 11: CI + release workflows + RELEASING doc (D6)

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `docs/RELEASING.md`

- [ ] **Step 11.1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main, "feat/**", "harden/**", "harness/**"]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install -e ".[dev]"
      - run: python -m pytest -q
      - run: python tools/feature_gate.py
      - run: python tools/build_webchat_skill.py --check
```

- [ ] **Step 11.2: Create `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write
  id-token: write # PyPI trusted publishing

jobs:
  release:
    runs-on: ubuntu-latest
    environment: pypi
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install build
      - run: python -m build # dist/*.tar.gz + dist/*.whl
      - run: python tools/build_webchat_skill.py --out dist-skill
      - name: Publish to PyPI (trusted publishing)
        uses: pypa/gh-action-pypi-publish@release/v1
      - name: Create GitHub release + attach skill zip
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "$GITHUB_REF_NAME" --generate-notes || true
          gh release upload "$GITHUB_REF_NAME" \
            dist-skill/paper-verify-webchat-skill.zip dist/* --clobber
```

- [ ] **Step 11.3: Create `docs/RELEASING.md`**

```markdown
# Releasing paper-verify

The release workflow (`.github/workflows/release.yml`) publishes to PyPI via
**trusted publishing** (no API token in the repo) and attaches the web-chat
skill zip to the GitHub release. Until the one-time PyPI link below is done,
the PyPI step fails at auth — expected, documented, nothing silent.

## One-time setup (maintainer)

1. Create/log in to a PyPI account → <https://pypi.org/manage/account/publishing/>.
2. Add a **pending publisher**:
   - PyPI project name: `paper-verify`
   - Owner: `nolainjin` / Repository: `paper-verify`
   - Workflow name: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo: Settings → Environments → create `pypi` (optionally
   require reviewers).

## Each release

1. Bump `version` in `pyproject.toml` (e.g. `0.2.0`); update README if the
   surface changed. Commit to `main`.
2. Tag and push:

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```

3. The workflow builds sdist+wheel, publishes to PyPI, creates the GitHub
   release, and attaches `paper-verify-webchat-skill.zip`.
4. Verify: `uvx paper-verify@latest --list-profiles` and the release page
   shows the zip.

## After first PyPI release

Update README install sections from `git+` forms to plain
`uvx paper-verify` / `pip install paper-verify[...]` (keep the git form as the
"latest from source" alternative).
```

- [ ] **Step 11.4: Commit**

```bash
git add .github/ docs/RELEASING.md
git commit -m "ci: pytest+gate workflow; release: PyPI trusted publishing + skill zip asset"
```

---

### Task 12: Feature registration + full gate (D8)

**Files:**
- Modify: `feature_list.json`

- [ ] **Step 12.1: Append 3 features** to the `features` array (before the closing `]`):

```json
    {
      "id": "W1",
      "name": "offline evidence scoring (--from-evidence)",
      "behavior": "External fetch/judge evidence JSON is scored with the standard rubric via report_from_evidence + CLI --from-evidence/--extract-only; malformed evidence exits 2 naming the citation index.",
      "verification": "python -m pytest tests/test_offline.py -q",
      "dependencies": [],
      "state": "not-started",
      "evidence": ""
    },
    {
      "id": "W2",
      "name": "webchat assets rubric drift guard",
      "behavior": "Portable prompts (EN/KO) and the webchat SKILL.md carry rubric numbers identical to score.py constants and reference the offline entrypoints.",
      "verification": "python -m pytest tests/test_webchat_assets.py -q",
      "dependencies": ["W1"],
      "state": "not-started",
      "evidence": ""
    },
    {
      "id": "W3",
      "name": "webchat skill zip builder",
      "behavior": "tools/build_webchat_skill.py deterministically bundles SKILL.md + the paperverify package + sample evidence + fallback prompt; --check verifies required members.",
      "verification": "python tools/build_webchat_skill.py --check",
      "dependencies": ["W1", "W2"],
      "state": "not-started",
      "evidence": ""
    }
```

- [ ] **Step 12.2: Gate** — `python tools/feature_gate.py` → expect `DRIFT` on W1–W3 (verified but marked not-started), 0 LIE. Then:

Run: `python tools/feature_gate.py --sync`
Expected: W1–W3 promoted to `passing` with dated evidence; exit 0; back-pressure 0.

- [ ] **Step 12.3: Full suite once more**

Run: `python -m pytest -q`
Expected: PASS (all)

- [ ] **Step 12.4: Commit**

```bash
git add feature_list.json
git commit -m "harness: register W1-W3 webchat/offline features (gate-promoted)"
```

---

### Task 13: Push + live acceptance + handoff

- [ ] **Step 13.1: Push the branch**

```bash
git push -u origin feat/webchat-and-cli-accessibility
```

Expected side effect: the `ci.yml` workflow runs on GitHub (live YAML check — acceptance #7). Check: `gh run list --branch feat/webchat-and-cli-accessibility --limit 3` after a minute; expect `completed success` for CI.

- [ ] **Step 13.2: Live uvx acceptance (#3)** — run **outside** the repo dir:

```bash
cd /tmp && uvx --from "git+https://github.com/nolainjin/paper-verify@feat/webchat-and-cli-accessibility" \
  paper-verify --list-profiles | head -c 200
```

Expected: JSON list of 4 profiles, exit 0. (Uses `--list-profiles` to avoid network fetches; proves the zero-install path end-to-end.)

- [ ] **Step 13.3: Report** — summarize: tests count, gate state, CI run URL, uvx output. Merge to main + tag/release remain user decisions (offer them).

---

## Self-review checklist (run after writing, before execution)

- Spec coverage: D1→T1-3, D2→T5, D3→T6-7, D4→T8, D5→T9, D6→T11, D7→T10, D8→T4+T12. Acceptance 1→T12.3, 2→T12.2, 3→T13.2, 4→T7.3, 5→T8.7, 6→T10, 7→T13.1. ✓
- No placeholders: all file contents are complete above. ✓
- Type consistency: `report_from_evidence(data: dict) -> Report`; `_emit(report, args, *, base, as_json, human) -> int`; `EvidenceError(ValueError)`; anchors built from `_CLAIM_POINTS`. ✓
