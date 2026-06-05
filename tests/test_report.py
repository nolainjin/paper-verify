"""render_markdown / render_json coverage (audit P1-10 / TC-01..15).

report.py was 13% covered — the human-facing Markdown renderer and the
machine-readable JSON serializer (SSoT for CLI --json and the MCP server) had
almost no tests. These lock the section logic: failure banner, tier
distribution, A-tier list-only vs detailed sections, the Uncertain re-check
section, the Inaccessible/archive summary, and the JSON schema contract.
"""

from paperverify.models import (
    Citation,
    Fetched,
    Judgement,
    Report,
    ScoredCitation,
    Tier,
    Verdict,
)
from paperverify.report import SCHEMA_VERSION, render_json, render_markdown


def _cite(id=1, ref="https://example.com/x", context="Smith reported X in 2017.", line=3):
    return Citation(id=id, type="URL", ref=ref, context=context, line=line)


def _sc(score, verdict=Verdict.MATCH, *, id=1, ref="https://example.com/x",
        fetched="ok", reason="because the source says so", line=3):
    """Build a ScoredCitation with a chosen score (=> tier) and consensus verdict.

    fetched: "ok" (200), "404" (inaccessible), "archive" (inaccessible via
    web.archive.org), or None (never fetched).
    """
    if fetched == "ok":
        f = Fetched(id=id, status=200, title="T", abstract="A")
    elif fetched == "404":
        f = Fetched(id=id, status=404, error="HTTP 404")
    elif fetched == "archive":
        f = Fetched(id=id, status=503, via_archive=True, error="HTTP 503")
    else:
        f = None
    return ScoredCitation(
        citation=_cite(id=id, ref=ref, line=line),
        fetched=f,
        judgements=[Judgement(judge="keyword", verdict=verdict, reason=reason)],
        score=score,
        effective_verdict=verdict,
    )


def _report(scored, source_file="docs/sample.md", level="L2", judges=("keyword",)):
    return Report(source_file=source_file, level=level, scored=list(scored),
                  judges=list(judges))


# --- render_markdown -------------------------------------------------------

def test_header_basics():
    out = render_markdown(_report([_sc(95)]))
    assert "# Citation Verification Report — sample.md" in out
    assert "- Source: `docs/sample.md`" in out
    assert "- Level: L2" in out
    assert "- Judges: keyword" in out
    assert "- Citations: 1" in out
    assert "Overall score: **95.0/100" in out


def test_failure_banner_present_when_F_tier():
    out = render_markdown(_report([_sc(95), _sc(10, Verdict.MISMATCH, id=2)]))
    assert "DOCUMENT-LEVEL WARNING" in out
    # the F section renders with worst-first ordering and a numbered detail line.
    assert "🔴 F (replace source — do not cite) — 1" in out
    assert "reason:" in out


def test_no_failure_banner_when_all_pass():
    out = render_markdown(_report([_sc(95), _sc(80, Verdict.PARTIAL, id=2)]))
    assert "DOCUMENT-LEVEL WARNING" not in out


def test_a_tier_is_list_only_no_detail():
    """A-tier citations are listed without the claim/reason detail block (spec)."""
    out = render_markdown(_report([_sc(95, ref="https://a.example/paper")]))
    assert "🟢 A (citable) — 1" in out
    assert "- `https://a.example/paper` (line 3, 95/100)" in out
    # A-tier list omits the per-item "claim:"/"reason:" detail lines.
    assert "claim:" not in out


def test_tier_distribution_table():
    # 1 A, 1 B, 2 C  -> shares 25/25/50/0
    scored = [_sc(95, id=1), _sc(80, Verdict.PARTIAL, id=2),
              _sc(60, Verdict.PARTIAL, id=3), _sc(55, Verdict.PARTIAL, id=4)]
    out = render_markdown(_report(scored))
    assert "| Tier | Count | Share |" in out
    assert "| 🟢 A | 1 | 25% |" in out
    assert "| 🟠 C | 2 | 50% |" in out
    assert "| 🔴 F | 0 | 0% |" in out


def test_uncertain_section():
    scored = [_sc(95, id=1), _sc(15, Verdict.UNCERTAIN, id=2, ref="https://u.example/q",
                                 reason="source insufficient")]
    out = render_markdown(_report(scored))
    assert "❓ Needs re-check (Uncertain) — 1" in out
    assert "https://u.example/q" in out
    assert "source insufficient" in out


def test_inaccessible_section_counts_archive():
    scored = [
        _sc(95, id=1),                                   # ok
        _sc(40, Verdict.INACCESSIBLE, id=2, fetched="404", ref="https://gone.example"),
        _sc(40, Verdict.INACCESSIBLE, id=3, fetched="archive", ref="https://arch.example"),
        _sc(40, Verdict.INACCESSIBLE, id=4, fetched=None, ref="https://never.example"),
    ]
    out = render_markdown(_report(scored))
    assert "Inaccessible / fetch issues — 3" in out
    assert "served via web.archive.org fallback: 1" in out
    assert "https://gone.example" in out
    assert "(archive)" in out  # the via_archive item is tagged


def test_empty_report_renders_zero():
    out = render_markdown(_report([]))
    assert "- Citations: 0" in out
    assert "Overall score: **0.0/100" in out
    # no divide-by-zero in the share column
    assert "| 🟢 A | 0 | 0% |" in out


def test_long_ref_is_excerpted():
    long_ref = "https://example.com/" + "a" * 200
    out = render_markdown(_report([_sc(95, ref=long_ref)]))
    assert "…" in out  # _excerpt truncates with an ellipsis


# --- render_json -----------------------------------------------------------

def test_render_json_schema_contract():
    rep = _report([_sc(95, id=1), _sc(10, Verdict.MISMATCH, id=2)])
    j = render_json(rep)
    assert j["schema_version"] == SCHEMA_VERSION == "4"
    assert j["source_file"] == "docs/sample.md"
    assert j["level"] == "L2"
    assert j["judges"] == ["keyword"]
    assert j["overall_tier"] in {"A", "B", "C", "F"}
    assert j["has_failure"] is True  # the id=2 Mismatch is tier F
    assert set(j["tier_distribution"]) == {"A", "B", "C", "F"}
    assert len(j["citations"]) == 2
    first = j["citations"][0]
    # P0-2: consensus and effective_verdict are exposed and equal.
    assert first["consensus"] == first["effective_verdict"] == "Match"


def test_render_json_profile_passthrough():
    rep = _report([_sc(95)])
    rep.profile = "claude-code"
    assert render_json(rep)["profile"] == "claude-code"
