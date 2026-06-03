# paper-verify

**Fact-check the citations in any document.** `paper-verify` extracts every
reference (URL / DOI / PMC / PMID / arXiv) from a Markdown or text file,
fetches each source, asks one or more LLMs whether the cited *claim* is actually
supported by the source, scores each citation on a transparent 100-point rubric,
and writes a Markdown report that flags fabricated, misquoted, or dead-link
citations.

> Built for researchers, grad students, lecturers, and bloggers who need to
> trust their own footnotes — and to catch AI-hallucinated citations before
> they ship.

## No framework dependency

The core has **zero required third-party dependencies** (Python stdlib only) and
**no dependency on any agent-orchestration framework**. Parallel fetching uses
`concurrent.futures.ThreadPoolExecutor`. LLM providers (Anthropic / OpenAI /
Gemini) are *optional extras*, and a dependency-free keyword judge lets the tool
run end-to-end with **no API keys at all**.

## What it does

1. **Extract** — regex-match citations and capture ~100 chars of surrounding
   context (the claim being made), with line numbers; deduped by `(type, ref)`.
2. **Fetch** — resolve each reference and fetch its source through an
   **explicit fallback chain** (see below). For academic identifiers
   (DOI / arXiv / PMID / PMC) it first queries free official **metadata APIs**
   (Crossref / arXiv / NCBI) — bypassing paywalls — then falls back to a direct
   HTTP fetch (browser-like User-Agent, 10 s timeout, follows redirects, strips
   HTML to text), then to the **Wayback Machine** (`web.archive.org`).
3. **Judge** — one or more pluggable judges decide a verdict
   (Match / Partial / Mismatch / Uncertain / Inaccessible) + a one-line reason.
   Multiple judges = independent cross-check; an optional `--tiebreak` judge
   resolves disagreements.
4. **Score** — apply the 100-point rubric and assign a tier.
5. **Report** — emit `<basename>_report.md` (+ `<basename>_claims.jsonl` for
   reuse). Any tier-F citation raises a document-level warning banner.

## Install

```bash
pip install -e .                  # core, stdlib only
pip install -e ".[anthropic]"     # + Anthropic judge
pip install -e ".[openai]"        # + OpenAI judge
pip install -e ".[gemini]"        # + Gemini judge
pip install -e ".[all]"           # all providers
pip install -e ".[mcp]"           # + MCP server (agent tool)
pip install -e ".[dev]"           # + pytest
```

Requires Python ≥ 3.10.

## Quickstart

Runs with **no API keys** (keyword judge, low confidence):

```bash
paper-verify examples/sample.md --level L2 --out /tmp/pv
# or, without installing:
python -m paperverify examples/sample.md --level L2 --out /tmp/pv
```

Output resembles:

```
paper-verify: 5 citations, level L2, judges: keyword
Overall: <score>/100 <tier>  [🟢A:<n> 🟡B:<n> 🟠C:<n> 🔴F:<n>]
⚠️  Document contains tier-F citations — see report.
Report:  /tmp/pv/sample_report.md
Claims:  /tmp/pv/sample_claims.jsonl
```

For a real fact-check, add an LLM judge:

```bash
export ANTHROPIC_API_KEY=sk-...
paper-verify paper.md --level L2 --judge anthropic:claude-sonnet-4-6
```

## Verification levels

| Level | Depth | Network / cost |
|---|---|---|
| **L1** | URL alive (HTTP 2xx) only — fast dead-link sweep, scored 100 for reachable / 0 for unreachable | network only, no LLM |
| **L2** | abstract / title vs. claim match (**default**) | network + ~1 LLM call per citation |
| **L3** | full content + claim/number alignment | network + several LLM calls per citation |

`L1` runs with no LLM at all. Pick the level with `--level L1|L2|L3`.

> **⚠️ L1 caveat.** L1 scores **reachability only** (HTTP-alive → 100,
> unreachable → 0). It does **not** verify that the page content supports the
> claim. Soft-404s (pages that return HTTP 200 with error / placeholder content)
> are now **detected heuristically** — a reachable-but-suspect page scores **50**
> (not a clean 100), with a `soft_404_suspect` flag in the output. The heuristic
> is **not perfect** (it checks error markers, deep-path→homepage redirects, and
> suspiciously tiny bodies); use **L2 / L3** for real content verification.

### Academic metadata (paywall bypass)

For academic identifiers (DOI / arXiv / PMID / PMC, or URLs that clearly carry
one), paper-verify queries free official **metadata APIs** before scraping HTML:

| Source | API | Yields |
|---|---|---|
| Crossref | `api.crossref.org/works/{doi}` | title, authors, year, abstract |
| arXiv | `export.arxiv.org/api/query?id_list={id}` | title, authors, year, abstract |
| NCBI | E-utilities `esummary` (PubMed) / idconv (PMC) | title, authors, year |

This returns structured **title / authors / year / abstract even when the
publisher landing page is a paywall stub**, and makes the author/year rubric a
real comparison instead of a fuzzy HTML match.

**Explicit, observable fallback chain** (each step only on failure of the prior;
the path that actually served the data is recorded in the `source` field, never
silently — per the No-Silent-Fallback principle):

```
metadata API (crossref|arxiv|ncbi)  →  HTTP fetch (http)  →  Wayback (archive)  →  none
```

- A metadata call uses a short timeout (~8 s) + one retry/backoff on transient
  errors (timeout / connection / HTTP 429 / 5xx); HTTP 404 means "not found"
  (no retry).
- A failed metadata lookup **never crashes the run** — it falls through, and
  `source` then reads `"http"` (so you can see the API did *not* serve it),
  `"archive"`, or `"none"`.
- If the whole chain fails, the citation is `source="none"`, `status=0`, carries
  an `error`, and is scored **Inaccessible** — no invented metadata, never scored
  as alive.

The `source` field lets you see at a glance whether a citation was
**metadata-verified** (`crossref`/`arxiv`/`ncbi`), **HTML-scraped** (`http`),
served from **archive**, or **unverifiable** (`none`).

## Judges & providers

Pass `--judge SPEC` (repeatable for cross-check). Spec forms:

| Spec | Judge | Requirement |
|---|---|---|
| `keyword` | token-overlap heuristic (**default**) | none — always available |
| `anthropic` / `anthropic:claude-sonnet-4-6` | Anthropic SDK | `pip install paper-verify[anthropic]`, `ANTHROPIC_API_KEY` |
| `openai` / `openai:gpt-4o-mini` | OpenAI SDK | `pip install paper-verify[openai]`, `OPENAI_API_KEY` |
| `gemini` / `gemini:gemini-2.0-flash` | google-genai SDK | `pip install paper-verify[gemini]`, `GEMINI_API_KEY` |
| `cli:gemini` / `cli:claude` / `cli:codex` | shells out to a locally-installed CLI | that CLI on `$PATH` |

The `keyword` judge is **clearly low-confidence** — it only measures lexical
overlap, not meaning. Use it to smoke-test the pipeline; use an LLM judge for
real verification.

## Harness profiles (`--profile`)

A **harness profile** bundles the recommended judge order and frontend setup for
a given agent (Claude Code, Cursor, Codex, Gemini). Pass `--profile <key>` and,
**when you do not pass any explicit `--judge`**, paper-verify defaults the judges
to that profile's recommended list, trying them **in order of availability** —
it skips any judge whose SDK/CLI is not installed and uses the first available
one(s), falling back to `keyword` (with a one-line stderr note) if none are
available. An explicit `--judge` always wins; the profile is still recorded.

```bash
paper-verify paper.md --profile claude-code --json
```

Keys (aliases like `claude` → `claude-code` are accepted): `claude-code`,
`cursor`, `codex`, `gemini`. The active profile is recorded in the JSON output
under the top-level `"profile"` field (`null` when unset). List every profile as
JSON (no file argument needed):

```bash
paper-verify --list-profiles | python -m json.tool
```

See [`docs/harness-strategy.md`](docs/harness-strategy.md) for the full matrix.

## How cross-check works

Supply two or more `--judge` flags. Each judge evaluates the same
`(claim_context, source_text)` independently. The verdict drives the claim-match
score; the **cross-check** rubric item awards 10 points only when judges **agree**
— so disagreement costs points and surfaces citations worth a human spot-check.

```bash
paper-verify paper.md \
  --judge anthropic:claude-sonnet-4-6 \
  --judge gemini:gemini-2.0-flash
```

**Tie-break (3rd judge).** When two or more judges disagree, pass an optional
`--tiebreak <spec>` judge. It runs **only on the split citations** and restores
the original 3-stage consensus spirit:

- on a genuine consensus (incl. **after** the tie-break) the citation keeps its
  10 cross-check points, using the **most-conservative** verdict as consensus;
- if judges remain split with **no** `--tiebreak`, the effective verdict becomes
  **Uncertain** (claim-match = 15) and cross-check = 0, flagging it for review.

```bash
paper-verify paper.md \
  --judge anthropic:claude-sonnet-4-6 \
  --judge openai:gpt-4o-mini \
  --tiebreak gemini:gemini-2.0-flash
```

A judge may also answer **Uncertain** on its own when the source is insufficient
to decide — better than guessing. Uncertain citations are grouped in a
**"Needs re-check"** section of the Markdown report.

## Using paper-verify from your own agent

paper-verify is **agent-callable** two ways. Both are provider-agnostic — pick
any judge (`keyword`, `anthropic`, `openai`, `gemini`, `cli:*`); the structured
output shape is identical regardless of judge.

Provider-specific harness profiles are documented in
[`docs/harness-strategy.md`](docs/harness-strategy.md), with frontend notes for
Claude Code, Cursor, Codex, and Gemini under [`docs/providers/`](docs/providers/).
The core pipeline stays shared; frontend differences live in profiles and docs
instead of long-lived provider branches.

### (a) `--json` — capture structured output from stdout

Add `--json` and the CLI writes the **full result as JSON to stdout** while the
human summary goes to stderr, so an agent can capture and parse stdout directly:

```bash
result=$(paper-verify paper.md --level L2 --judge keyword --json)
echo "$result" | python -m json.tool
# overall_score / overall_tier / has_failure live at the top level:
echo "$result" | python -c "import sys,json; d=json.load(sys.stdin); print(d['overall_score'], d['overall_tier'], d['has_failure'])"
```

The JSON top-level keys are: `schema_version`, `source_file`, `level`,
`profile` (active harness profile key, or `null`), `judges`, `overall_score`,
`overall_tier`, `has_failure`, `tier_distribution` (counts per tier), and
`citations` (one object per citation: `citation`, `fetched`, `judgements`,
`score`, `breakdown`, `tier`).

Each `citation.fetched` object carries (schema_version `"3"`): `status`,
`title`, `abstract`, `url_final`, `via_archive`, `error`, plus `authors` (list,
from metadata APIs), `year` (int or `null`), `source` (`crossref` | `arxiv` |
`ncbi` | `http` | `archive` | `none` — which path produced the data), and
`soft_404_suspect` (bool — a 2xx page that looks like an error/placeholder).

`--json` is **additive**: pass `--out DIR` to also write the `.md` / `.jsonl`
files. **Exit codes are stable for agents**: `0` = ran successfully *regardless
of grades* (a tier-F document still exits 0 — inspect `has_failure`); a nonzero
code (`2`) means a real error (file not found, bad judge spec).

### (b) MCP server — register as a tool

`paper-verify[mcp]` ships an MCP server (stdio transport) exposing these tools:

| Tool | Purpose |
|---|---|
| `verify_file(path, level="L2", judges=["keyword"], workers=4, tiebreak=None)` | full pipeline on a file → structured dict |
| `verify_text(text, level="L2", judges=["keyword"], tiebreak=None)` | same, on raw document text |
| `extract_citations(text)` | extraction only — no network, no LLM |
| `list_profiles()` | list all harness profiles → list of dicts (self-discovery) |
| `get_profile(key)` | look up one profile by key/alias → dict (`{"error": ...}` if unknown) |

```bash
pip install paper-verify[mcp]
```

Register it with an MCP client. For Claude Code:

```bash
claude mcp add paper-verify -- paper-verify-mcp
```

Or in an MCP client config (`mcpServers`):

```json
{
  "mcpServers": {
    "paper-verify": {
      "command": "paper-verify-mcp"
    }
  }
}
```

The `mcp` package is an **optional extra** — the core tool and `--json` work
with `mcp` not installed; importing the server without it raises a clear
`pip install paper-verify[mcp]` hint instead of crashing.

## Scoring rubric (100 points)

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | source fetched with HTTP 2xx |
| Author / year match | 20 / 10 / 0 | author **and** year align = 20; only one = 10; neither = 0. When no metadata is available to compare, the slot is **neutral** (10, "metadata unavailable") rather than a misleading 0 |
| Claim match | 50 | Match = 50 · Partial = 25 · **Uncertain = 15** · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | judges agree (incl. after a `--tiebreak`); a split with no tie-break scores 0 and marks the citation **Uncertain** |

At **L1**, a reachable page scores 100, a **soft-404-suspect** reachable page
scores **50**, and an unreachable page scores 0.

**Document score** = average of per-citation scores. If **any** citation is
tier F, the whole document is flagged ⚠️.

## Verdict & tier taxonomy

Verdicts (per judge):

| Verdict | Meaning |
|---|---|
| ✅ Match | claim is explicitly supported by the source |
| ⚠️ Partial | partially supported; numbers / year / nuance differ |
| ❌ Mismatch | absent from, or contradicted by, the source |
| ❓ Uncertain | source insufficient to decide (or judges split, no tie-break) — flagged for human review |
| ⚫ Inaccessible | paywall / 404 / timeout — could not verify |

Tiers (per citation, and document average):

| Tier | Score | Meaning |
|---|---|---|
| 🟢 A | 90–100 | citable in a thesis / formal report |
| 🟡 B | 70–89 | fine for a lecture / blog, minor fixes |
| 🟠 C | 50–69 | must be re-checked |
| 🔴 F | 0–49 | do not cite — replace the source |

## Limitations

- **AI judgment reliability** — LLM judges can mis-read an abstract's meaning.
  Cross-check with a second judge (+ `--tiebreak`) and spot-check important
  citations by hand. A judge may answer **Uncertain** rather than guess.
- **Paywalls** — for DOI / arXiv / PMID / PMC the free metadata APIs return the
  title / authors / year / abstract even behind a paywall. Full-text claims that
  need the body (not just the abstract) may still land in tier C — expected.
- **Soft-404 detection is heuristic** — it catches common error markers, deep
  path→homepage redirects, and tiny bodies, but can miss disguised error pages.
- **Archive misses** — when a URL is dead *and* absent from the Wayback Machine,
  the fallback fails and the citation is marked Inaccessible.
- **JavaScript-heavy pages** — SPA pages may return little text to the stripper.
- The **keyword** judge measures lexical overlap only and is not a substitute
  for semantic verification.

## 한국어 요약

`paper-verify`는 문서(마크다운/텍스트) 안의 인용 출처(URL·DOI·PMC·PMID·arXiv)를
자동으로 추출하고, 각 출처를 가져와 **인용된 주장이 원문에 실제로 존재하는지**를
LLM 심판으로 판정한 뒤 **100점 채점표**로 점수를 매겨 보고서를 만듭니다. AI가
지어낸(hallucinated) 인용·오인용·죽은 링크를 발행 전에 잡아내기 위한 도구입니다.

- **외부 프레임워크 의존성 0** — 코어는 파이썬 표준 라이브러리만 사용하며, LLM
  제공자는 선택적 extra입니다. API 키 없이도 `keyword` 심판으로 전 과정이 동작합니다
  (단, 저신뢰도).
- 사용: `paper-verify <파일> --level L2 --judge anthropic:claude-sonnet-4-6`
- 레벨: `L1`(링크 생존만) / `L2`(기본, 초록·제목 일치) / `L3`(본문·수치 정합).

## License

MIT © 2026 Duchan Jin (진두찬). See [LICENSE](LICENSE).
