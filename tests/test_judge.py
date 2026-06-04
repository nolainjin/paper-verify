"""Tests for paperverify.judge.parse_response and KeywordJudge."""

from paperverify.judge import parse_response
from paperverify.models import Verdict


def test_parse_response_prefers_verdict_line_over_prose_before_it():
    """Prose mentioning a verdict word before the explicit VERDICT: line
    must not override the structured verdict (audit CL-2 / P0-5)."""
    text = "This is a partial match at best.\nVERDICT: Mismatch\nREASON: numbers differ"
    verdict, reason = parse_response(text)
    assert verdict is Verdict.MISMATCH


def test_parse_response_prefers_verdict_line_when_prose_says_uncertain():
    text = "I would call this uncertain given the evidence.\nVERDICT: Match\nREASON: actually supported"
    verdict, _ = parse_response(text)
    assert verdict is Verdict.MATCH


def test_parse_response_canonical_format_still_works():
    verdict, reason = parse_response("VERDICT: Uncertain\nREASON: source does not settle it")
    assert verdict is Verdict.UNCERTAIN
    assert "settle" in reason


def test_parse_response_bare_word_without_verdict_line_still_parses():
    """No explicit VERDICT: line — fall back to first verdict word."""
    verdict, _ = parse_response("Mismatch — the figures disagree")
    assert verdict is Verdict.MISMATCH
