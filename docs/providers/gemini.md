# Gemini Harness

## Recommended Surface

Use Gemini either as an SDK-backed judge or a local CLI judge:

```bash
paper-verify path/to/paper.md --level L2 --judge gemini --json
paper-verify path/to/paper.md --level L2 --judge cli:gemini --json
```

## Prompt Contract

Gemini should return the strict judge format:

```text
VERDICT: <Match|Partial|Mismatch|Inaccessible>
REASON: <one line>
```

The parser is intentionally tolerant, but strict output keeps cross-checks stable.

## Failure Policy

Use Gemini as a second-opinion judge when high-stakes citations need semantic review. If the local CLI is unavailable, fall back to `keyword` for smoke tests and document that semantic verification was not completed.

