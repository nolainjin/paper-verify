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
   {"source_file": "doc.md", "level": "L2", "citations": [ ...evidence items... ]}
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
