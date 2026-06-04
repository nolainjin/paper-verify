---
name: paper-verify
description: Use when the user asks to verify citations, audit source links, check blog/report/lecture references, or reduce a long bibliography to sources that need human review using the paper-verify CLI or MCP tools.
---

# Paper Verify

## Workflow

Run `paper-verify` as a source triage engine:

```bash
paper-verify <file> --level L1 --json
paper-verify <file> --level L2 --judge keyword --json
```

Use provider judges only when available and useful:

```bash
paper-verify <file> --level L2 --judge anthropic --judge openai --tiebreak gemini --json
```

## Triage

Use JSON fields for decisions:

- `tier`: prioritize `F` and `C`.
- `consensus` / `effective_verdict`: prioritize `Mismatch`, `Uncertain`, and `Inaccessible`.
- `judgements`: prioritize judge disagreement.
- `fetched.soft_404_suspect`: prioritize reachable pages that look like dead pages.
- `fetched.landing_status`: prioritize non-2xx landing pages.
- `fetched.source`: prioritize `archive` and `none`.

Return:

```markdown
## Must Review
- source — reason

## Review If Important
- source — reason

## Probably Safe
- source — reason
```

Do not treat reachable URLs as proof that claims are supported. Do not invent replacement sources.

