# Claude Code Harness

## Recommended Surface

Use the MCP server as the primary interface:

```bash
pip install paper-verify[mcp]
claude mcp add paper-verify -- paper-verify-mcp
```

Claude Code should call `verify_file`, `verify_text`, or `extract_citations` and inspect the returned dict.

## Skill Shape

- Name the tool as a citation verifier, not a general research agent.
- Tell Claude to gate on `has_failure` and `tier_distribution`.
- Keep expensive `L3` checks behind explicit user intent.
- Prefer `anthropic` for semantic checks and `keyword` for smoke tests.

## Failure Policy

If a provider key or SDK is missing, paper-verify records that judge as `Inaccessible` instead of killing the whole run. Claude should surface the failed judge and continue with the available evidence.

