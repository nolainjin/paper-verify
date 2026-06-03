# Codex Harness

## Recommended Surface

Use CLI JSON for deterministic local workflows:

```bash
paper-verify path/to/paper.md --level L1 --json
paper-verify path/to/paper.md --level L2 --judge keyword --json
```

MCP can be added when the Codex environment supports it, but CLI JSON is the baseline.

## Skill Shape

- Put repository rules in `AGENTS.md`.
- Put reusable workflow instructions in a Codex skill.
- Gate PR readiness on `has_failure`, `overall_tier`, and targeted citation rows.
- Add tests when changing parser, scoring, or JSON schema behavior.

## Failure Policy

Codex should continue after individual fetch or judge failures, then report the failed citation rows. It should not infer correctness from the Markdown report alone.

