"""H2 (report side): a keyword-only run is a token-overlap heuristic, not a
semantic verification. The report must say so, so a reader does not read an
80/100 Tier B from the no-API-key default path as "the claim was checked".

When every judge used is the dependency-free ``keyword`` judge (no LLM judge),
render_markdown surfaces a caveat banner and render_json sets a
``keyword_only`` flag. Runs that include a real LLM judge are unaffected.
"""

from paperverify.models import (
    Citation,
    Fetched,
    Judgement,
    Report,
    ScoredCitation,
    Verdict,
)
from paperverify.report import render_json, render_markdown


def _sc(verdict=Verdict.MATCH, judge="keyword"):
    return ScoredCitation(
        citation=Citation(id=1, type="URL", ref="https://x", context="c", line=1),
        fetched=Fetched(id=1, status=200, title="T", abstract="A"),
        judgements=[Judgement(judge=judge, verdict=verdict, reason="r")],
        score=80.0,
        effective_verdict=verdict,
    )


def _report(judges):
    return Report(source_file="d.md", level="L2", scored=[_sc()], judges=list(judges))


def test_keyword_only_markdown_has_manual_check_caveat():
    out = render_markdown(_report(["keyword"]))
    low = out.lower()
    assert "keyword" in low
    assert "heuristic" in low or "manual" in low or "not a semantic" in low


def test_keyword_only_json_flag_true():
    assert render_json(_report(["keyword"]))["keyword_only"] is True


def test_llm_judge_run_no_caveat():
    out = render_markdown(_report(["anthropic:claude-sonnet-4-6"]))
    assert "semantic verification (token overlap only)" not in out.lower()
    assert render_json(_report(["anthropic:claude-sonnet-4-6"]))["keyword_only"] is False


def test_mixed_judges_not_keyword_only():
    # keyword + a real judge -> a semantic judge ran, so not keyword-only.
    assert render_json(_report(["keyword", "gemini:gemini-2.0-flash"]))["keyword_only"] is False
