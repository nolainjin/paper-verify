---
name: paper-verify
description: Use when the user asks to verify citations, check source links, reduce the number of papers or sources needing human review, audit blog/report/lecture references, or run paper-verify on Markdown/text documents via CLI JSON or MCP.
---

# Paper Verify

## Purpose

Use `paper-verify` as a citation verification and source triage engine. It does not replace expert review; it narrows the review set to risky citations and sources.

## Inputs

- Markdown or plain text documents with URLs, DOI, PMC, PMID, or arXiv references.
- Existing `paper-verify` JSON output when the user wants triage or summarization only.

## Workflow

1. Locate the document and confirm it is safe to inspect.
2. Run a cheap preflight for broad link health:

```bash
paper-verify <file> --level L1 --json
```

3. Run semantic checking when claims need support verification:

```bash
paper-verify <file> --level L2 --judge keyword --json
```

4. If provider judges are available and the document is high stakes, use cross-checking:

```bash
paper-verify <file> --level L2 \
  --judge anthropic \
  --judge openai \
  --tiebreak gemini \
  --json
```

5. Parse JSON fields, not Markdown prose, for decisions.
6. Produce a human-review shortlist before editing or publishing.

## Triage Rules

Classify as `Must Review` when any citation has:

- `tier` equal to `F`.
- `consensus` or `effective_verdict` equal to `Mismatch`, `Uncertain`, or `Inaccessible`.
- judge disagreement in `judgements`.
- `fetched.soft_404_suspect` equal to `true`.
- `fetched.source` equal to `archive` or `none`.
- non-2xx `fetched.landing_status` while metadata resolved the source.

Classify as `Review If Important` when:

- `tier` is `C`.
- only author/year partially matches.
- the claim is central to the user's argument.

Classify as `Probably Safe` only when:

- `tier` is `A` or `B`.
- `consensus` is `Match` or `Partial`.
- there is no soft-404, dead landing, archive-only source, or judge disagreement.

## Output Shape

Return concise Markdown:

```markdown
## Must Review
- <source> — <reason>

## Review If Important
- <source> — <reason>

## Probably Safe
- <source> — <reason>
```

If the user asks for files, write the generated report or shortlist to the requested path.

## Guardrails

- Do not claim a citation is true solely because it is reachable.
- Treat `keyword` as a smoke-test judge, not a semantic authority.
- Do not invent replacement citations.
- Keep generated `*_report.md` and `*_claims.jsonl` out of commits unless the user asks.
- For publication, thesis, legal, medical, or financial use, state that final human review is still required.

