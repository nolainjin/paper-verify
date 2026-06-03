# Harness Strategy

paper-verify keeps one core verification pipeline and separates frontend-specific behavior into harness profiles. This avoids long-lived provider branches while still giving Claude Code, Cursor, Codex, and Gemini their own setup path.

## Principle

- Core stays shared: extraction, fetching, judging, scoring, Markdown reports, JSON output, and MCP output are the same for every frontend.
- Harness profiles vary by frontend: configuration files, command surface, skill wording, default judge choice, and failure handling differ.
- Branches are temporary delivery vehicles: use branches to introduce or test a profile, then merge back to the main code line.

## Profile Matrix

| Profile | Best surface | Skill/config anchor | Default judge path | Use when |
|---|---|---|---|---|
| Claude Code | MCP stdio | `CLAUDE.md`, `.claude/agents`, MCP config | `anthropic`, `keyword` fallback | Tool-calling loop inside Claude Code |
| Cursor | CLI JSON | `.cursor/rules` / workspace rules | `openai`, `keyword` fallback | Current-file or editor-driven verification |
| Codex | CLI JSON, optional MCP | `AGENTS.md`, Codex skills | `keyword`, `openai`, `cli:codex` | Repo review, tests, PR preparation |
| Gemini | CLI judge / JSON | prompt contract, `GEMINI.md` | `gemini`, `cli:gemini`, `keyword` | Long-context second opinion or cross-check |

## Recommended Branch Policy

Use feature branches for profile development only:

```bash
git switch -c codex/paper-verify-harness-profiles
```

Do not keep permanent provider branches such as `claude-code`, `cursor`, `codex`, and `gemini`. They will drift from the verifier core. Instead, keep provider-specific differences in:

- `paperverify/harness/`
- `docs/providers/`
- frontend config examples

## Gating Contract

Agents should read structured output instead of prose:

- `has_failure`: document has at least one tier-F citation.
- `overall_score`: average citation score.
- `overall_tier`: document tier.
- `tier_distribution`: counts by `A`, `B`, `C`, `F`.
- `citations[].breakdown`: score details.

For automation, `L1` should be the cheap preflight. `L2` should be the default semantic check. `L3` should be explicit because it may be slower and more expensive.

> **⚠️ L1 caveat.** L1 scores **reachability only** (HTTP-alive → 100, unreachable → 0). It does **not** verify that the page content supports the claim, and it **cannot detect soft-404s** (pages returning HTTP 200 with error/placeholder content). Use **L2 / L3** for content verification.

