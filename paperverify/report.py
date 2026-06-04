"""Markdown report rendering (SKILL §5)."""

from __future__ import annotations

import datetime
import os

from .models import Report, ScoredCitation, Tier, Verdict

# Schema version for the machine-readable JSON surface (render_json / MCP).
# Bump on any breaking change to the output shape.
#   "1" -> "2": added optional top-level "profile" field (active harness
#   profile key, or null). Additive, but bumped per repo convention.
#   "2" -> "3": added per-citation Fetched fields "authors" (list), "year"
#   (int|null), "source" (str: crossref|arxiv|ncbi|http|archive|none), and
#   "soft_404_suspect" (bool); added the "Uncertain" verdict. Additive.
#   "3" -> "4": added per-citation consensus/effective_verdict fields so JSON
#   consumers can read the verdict used for scoring directly.
SCHEMA_VERSION = "4"

_TIER_ORDER = [Tier.F, Tier.C, Tier.B, Tier.A]
_TIER_LABEL = {
    Tier.F: "F (replace source — do not cite)",
    Tier.C: "C (must re-check)",
    Tier.B: "B (minor fixes)",
    Tier.A: "A (citable)",
}


def _excerpt(text: str, n: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _detail_line(sc: ScoredCitation) -> str:
    c = sc.citation
    v = sc.consensus
    vsym = v.symbol if v else "⚫"
    reason = sc.judgements[0].reason if sc.judgements else ""
    head = f"{vsym} **{c.type}** `{_excerpt(c.ref, 80)}` (line {c.line}, {sc.score:.0f}/100)"
    body = f"   - claim: {_excerpt(c.context)}"
    if reason:
        body += f"\n   - reason: {_excerpt(reason)}"
    return head + "\n" + body


def render_markdown(report: Report) -> str:
    """Render a :class:`Report` to the SKILL §5 Markdown format."""
    today = datetime.date.today().isoformat()
    basename = os.path.basename(report.source_file)
    overall = report.overall_score
    otier = report.overall_tier

    lines: list[str] = []
    lines.append(f"# Citation Verification Report — {basename}")
    lines.append("")
    if report.has_failure:
        lines.append(
            "> ⚠️ **DOCUMENT-LEVEL WARNING** — at least one citation is tier 🔴 F "
            "(unsupported / fabricated). Address the F items before relying on this document."
        )
        lines.append("")
    lines.append(f"- Date: {today}")
    lines.append(f"- Source: `{report.source_file}`")
    lines.append(f"- Level: {report.level}")
    lines.append(f"- Judges: {', '.join(report.judges) if report.judges else '(none)'}")
    lines.append(f"- Citations: {len(report.scored)}")
    lines.append(f"- Overall score: **{overall}/100 {otier.symbol} {otier.value}**")
    lines.append("")

    # Tier distribution
    dist = report.tier_distribution()
    total = len(report.scored) or 1
    lines.append("## Tier distribution")
    lines.append("")
    lines.append("| Tier | Count | Share |")
    lines.append("|---|---|---|")
    for t in [Tier.A, Tier.B, Tier.C, Tier.F]:
        n = dist.get(t, 0)
        lines.append(f"| {t.symbol} {t.value} | {n} | {round(100 * n / total)}% |")
    lines.append("")

    # Sections, worst first
    by_tier: dict[Tier, list[ScoredCitation]] = {t: [] for t in Tier}
    for sc in report.scored:
        by_tier[sc.tier].append(sc)

    for t in _TIER_ORDER:
        items = by_tier[t]
        if not items:
            continue
        lines.append(f"## {t.symbol} {_TIER_LABEL[t]} — {len(items)}")
        lines.append("")
        if t is Tier.A:
            # A tier: list only (details omitted per spec).
            for sc in items:
                c = sc.citation
                lines.append(f"- `{_excerpt(c.ref, 80)}` (line {c.line}, {sc.score:.0f}/100)")
        else:
            for i, sc in enumerate(items, 1):
                lines.append(f"{i}. {_detail_line(sc)}")
        lines.append("")

    # Needs re-check — citations whose consensus verdict is Uncertain (the judge
    # abstained / judges split without a tie-break). Surfaced for human review.
    uncertain = [sc for sc in report.scored if sc.consensus is Verdict.UNCERTAIN]
    if uncertain:
        lines.append(f"## ❓ Needs re-check (Uncertain) — {len(uncertain)}")
        lines.append("")
        lines.append(
            "> Source was insufficient to decide, or judges split with no tie-break. "
            "Spot-check these by hand."
        )
        lines.append("")
        for sc in uncertain:
            c = sc.citation
            reason = sc.judgements[0].reason if sc.judgements else ""
            line = f"- `{_excerpt(c.ref, 80)}` (line {c.line}, {sc.score:.0f}/100)"
            if reason:
                line += f" — {_excerpt(reason)}"
            lines.append(line)
        lines.append("")

    # Inaccessible summary
    inaccessible = [
        sc for sc in report.scored
        if (sc.fetched is None or not sc.fetched.ok)
    ]
    if inaccessible:
        archived = sum(1 for sc in inaccessible if sc.fetched and sc.fetched.via_archive)
        lines.append(f"## Inaccessible / fetch issues — {len(inaccessible)}")
        lines.append("")
        lines.append(f"- served via web.archive.org fallback: {archived}")
        lines.append("")
        for sc in inaccessible:
            c = sc.citation
            f = sc.fetched
            status = f.status if f else 0
            err = (f.error if f else "") or f"HTTP {status}"
            arch = " (archive)" if (f and f.via_archive) else ""
            lines.append(f"- `{_excerpt(c.ref, 80)}` — {err}{arch}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(report: Report) -> dict:
    """Render a :class:`Report` to a machine-readable dict (SSoT for JSON output).

    This is the single serializer shared by the CLI ``--json`` flag and the MCP
    server, so the structured shape never diverges between the two surfaces.

    Keys:
        schema_version     str   — output contract version (see SCHEMA_VERSION)
        source_file        str
        level              str   — "L1" | "L2" | "L3"
        profile            str   — active harness profile key, or null
        judges             list  — judge names actually used
        overall_score      float
        overall_tier       str   — "A" | "B" | "C" | "F"
        has_failure        bool  — any citation is tier F
        tier_distribution  dict  — {"A": int, "B": int, "C": int, "F": int}
        citations          list  — each item is ScoredCitation.to_dict()
    """
    dist = report.tier_distribution()
    return {
        "schema_version": SCHEMA_VERSION,
        "source_file": report.source_file,
        "level": report.level,
        "profile": report.profile,
        "judges": list(report.judges),
        "overall_score": report.overall_score,
        "overall_tier": report.overall_tier.value,
        "has_failure": report.has_failure,
        "tier_distribution": {t.value: dist.get(t, 0) for t in Tier},
        "citations": [sc.to_dict() for sc in report.scored],
    }
