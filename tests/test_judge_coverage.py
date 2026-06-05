"""Characterization coverage for paperverify.judge.

Targets the uncovered branches the existing test_judge.py / test_p1_robustness.py
leave open: build_prompt truncation, parse_response pass-2 reason branches,
KeywordJudge empty-claim / Inaccessible / clamp edges, every SDK-backed judge's
``evaluate`` (clients fully mocked — zero real API calls), CLIJudge argv shapes
and subprocess outcomes (subprocess.run mocked — zero real processes), and the
make_judge / ensure_judge factory + availability-probe branches.

All tests are deterministic: no network, no real subprocess, no clock/random.
"""

import subprocess
import sys
import types

import pytest

from paperverify import judge as judge_mod
from paperverify.judge import (
    AnthropicJudge,
    CLIJudge,
    GeminiJudge,
    KeywordJudge,
    OpenAIJudge,
    build_prompt,
    ensure_judge,
    make_judge,
    parse_response,
)
from paperverify.models import Judgement, Verdict


# ---------------------------------------------------------------------------
# build_prompt — truncation (line 60-63)
# ---------------------------------------------------------------------------


def test_build_prompt_truncates_claim_and_source():
    prompt = build_prompt("c" * 5000, "s" * 20000)
    # claim capped at 2000, source at 8000 (see build_prompt).
    assert "c" * 2000 in prompt
    assert "c" * 2001 not in prompt
    assert "s" * 8000 in prompt
    assert "s" * 8001 not in prompt


def test_build_prompt_handles_empty_source():
    # ``(source_text or "")`` branch — None source must not raise.
    prompt = build_prompt("claim", None)  # type: ignore[arg-type]
    assert "claim" in prompt


# ---------------------------------------------------------------------------
# parse_response — pass-2 fallback reason branches (lines 112-122)
# ---------------------------------------------------------------------------


def test_parse_response_pass2_reason_on_same_line_after_word():
    # No "VERDICT:" line; verdict word + reason on the same line -> reason kept
    # from the remainder of that line (lines 113-116).
    v, reason = parse_response("Mismatch the cited figure is absent from the source")
    assert v is Verdict.MISMATCH
    assert reason == "the cited figure is absent from the source"


def test_parse_response_pass2_reason_from_explicit_reason_line():
    # Verdict word alone on its line, then an explicit "REASON:" line as the
    # very next non-empty line (line 117-119 break path).
    v, reason = parse_response("Partial\nREASON: numbers differ slightly")
    assert v is Verdict.PARTIAL
    assert reason == "numbers differ slightly"


def test_parse_response_pass2_first_following_line_wins_over_reason():
    # Characterization: when a plain line precedes the REASON: line, pass-2
    # takes that first non-empty line as the reason and stops (line 120-122),
    # so the later REASON: line is never consulted.
    v, reason = parse_response("Partial\nsome filler\nREASON: numbers differ slightly")
    assert v is Verdict.PARTIAL
    assert reason == "some filler"


def test_parse_response_pass2_reason_from_next_nonempty_line():
    # Verdict word alone, no REASON: line -> first following non-empty line is
    # taken as the reason (lines 120-122).
    v, reason = parse_response("Uncertain\n\nthe source does not settle it")
    assert v is Verdict.UNCERTAIN
    assert reason == "the source does not settle it"


def test_parse_response_no_verdict_word_defaults_to_partial():
    # Neither a VERDICT: line nor any verdict word -> default Partial, no reason.
    v, reason = parse_response("totally unrelated prose with no tier word")
    assert v is Verdict.PARTIAL
    assert reason == ""


def test_parse_response_verdict_line_reason_pulled_from_trailing_line():
    # VERDICT: line with nothing trailing, REASON: on a later line exercises
    # _trailing_reason (line 82).
    v, reason = parse_response("VERDICT: Match\n\nREASON: clearly supported")
    assert v is Verdict.MATCH
    assert reason == "clearly supported"


def test_parse_response_verdict_line_no_reason_anywhere():
    # VERDICT: line with no inline reason and no trailing REASON: line ->
    # _trailing_reason exhausts its loop and returns "" (line 82).
    v, reason = parse_response("VERDICT: Match")
    assert v is Verdict.MATCH
    assert reason == ""


def test_parse_response_reason_truncated_to_300():
    v, reason = parse_response("VERDICT: Match\nREASON: " + "z" * 500)
    assert v is Verdict.MATCH
    assert len(reason) == 300


# ---------------------------------------------------------------------------
# KeywordJudge — empty source, empty claim, clamp paths
# ---------------------------------------------------------------------------


def test_keyword_judge_empty_source_is_inaccessible():
    j = KeywordJudge().evaluate("any claim", "")
    assert j.verdict is Verdict.INACCESSIBLE
    assert "empty" in j.reason


def test_keyword_judge_whitespace_only_source_is_inaccessible():
    j = KeywordJudge().evaluate("any claim", "   \n\t  ")
    assert j.verdict is Verdict.INACCESSIBLE


def test_keyword_judge_no_content_tokens_in_claim_is_partial():
    # Claim has only stopwords / short tokens -> claim_tok empty (line 180).
    j = KeywordJudge().evaluate("the a an of to", "rich source text about science")
    assert j.verdict is Verdict.PARTIAL
    assert "no content tokens" in j.reason


def test_keyword_judge_low_overlap_is_mismatch():
    j = KeywordJudge().evaluate(
        "quantum chromodynamics lattice gauge",
        "a completely unrelated cooking recipe blog post",
    )
    assert j.verdict is Verdict.MISMATCH
    assert "overlap" in j.reason


def test_keyword_judge_partial_overlap_band():
    # Overlap in [0.2, 0.5) -> Partial. No numeric tokens so no clamp.
    claim = "alpha beta gamma delta epsilon"  # 5 content tokens
    source = "alpha beta unrelated words here entirely"  # 2 of 5 overlap = 40%
    j = KeywordJudge().evaluate(claim, source)
    assert j.verdict is Verdict.PARTIAL


def test_keyword_judge_clamp_reason_lists_missing_figure():
    # Match-by-overlap but claim number absent -> clamp to Partial AND the
    # reason names the missing figure (lines 196-202).
    claim = "retention improved by 42 percent across the whole study cohort"
    source = "retention improved across the whole study cohort overall here"
    j = KeywordJudge().evaluate(claim, source)
    assert j.verdict is Verdict.PARTIAL
    assert "42" in j.reason
    assert "absent" in j.reason


def test_keyword_judge_match_when_numbers_present():
    claim = "retention improved by 42 percent across the study cohort"
    source = "retention improved by 42 percent across the study cohort overall"
    j = KeywordJudge().evaluate(claim, source)
    assert j.verdict is Verdict.MATCH


# ---------------------------------------------------------------------------
# AnthropicJudge — empty source, missing SDK, mocked client
# ---------------------------------------------------------------------------


def test_anthropic_judge_empty_source_short_circuits():
    j = AnthropicJudge().evaluate("claim", "")
    assert j.verdict is Verdict.INACCESSIBLE


def test_anthropic_judge_missing_sdk_raises_runtime(monkeypatch):
    # Force ``import anthropic`` to fail inside evaluate (line 224-225).
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match="anthropic"):
        AnthropicJudge().evaluate("claim", "real source text")


def test_anthropic_judge_parses_mocked_client(monkeypatch):
    # Inject a fake ``anthropic`` module so evaluate runs without the SDK and
    # without any network call (lines 226-234).
    class _Block:
        text = "VERDICT: Match\nREASON: supported by abstract"

    class _Msg:
        content = [_Block()]

    class _Client:
        def __init__(self, *a, **k):
            pass

        class messages:  # noqa: N801 — mirrors anthropic client shape
            @staticmethod
            def create(**kwargs):
                # capture that the model + prompt were threaded through
                assert kwargs["model"] == "claude-sonnet-4-6"
                assert "CLAIM" in kwargs["messages"][0]["content"]
                return _Msg()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    j = AnthropicJudge().evaluate("claim text", "real source text")
    assert j.verdict is Verdict.MATCH
    assert "supported" in j.reason
    assert j.judge == "anthropic:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# OpenAIJudge — empty source, missing SDK, mocked client
# ---------------------------------------------------------------------------


def test_openai_judge_empty_source_short_circuits():
    j = OpenAIJudge().evaluate("claim", "   ")
    assert j.verdict is Verdict.INACCESSIBLE


def test_openai_judge_missing_sdk_raises_runtime(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(RuntimeError, match="openai"):
        OpenAIJudge().evaluate("claim", "real source text")


def test_openai_judge_parses_mocked_client(monkeypatch):
    class _Choice:
        class message:  # noqa: N801
            content = "VERDICT: Mismatch\nREASON: contradicted"

    class _Resp:
        choices = [_Choice()]

    class _Client:
        def __init__(self, *a, **k):
            pass

        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    assert kwargs["model"] == "gpt-4o-mini"
                    return _Resp()

    fake = types.ModuleType("openai")
    fake.OpenAI = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)

    j = OpenAIJudge().evaluate("claim", "real source")
    assert j.verdict is Verdict.MISMATCH
    assert "contradicted" in j.reason


def test_openai_judge_handles_none_content(monkeypatch):
    # ``resp.choices[0].message.content or ""`` branch when content is None.
    class _Choice:
        class message:  # noqa: N801
            content = None

    class _Resp:
        choices = [_Choice()]

    class _Client:
        def __init__(self, *a, **k):
            pass

        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    return _Resp()

    fake = types.ModuleType("openai")
    fake.OpenAI = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)

    j = OpenAIJudge().evaluate("claim", "real source")
    # Empty model text -> parse_response default Partial, no crash.
    assert j.verdict is Verdict.PARTIAL


# ---------------------------------------------------------------------------
# GeminiJudge — empty source, missing SDK, mocked client
# ---------------------------------------------------------------------------


def test_gemini_judge_empty_source_short_circuits():
    j = GeminiJudge().evaluate("claim", "")
    assert j.verdict is Verdict.INACCESSIBLE


def test_gemini_judge_missing_sdk_raises_runtime(monkeypatch):
    # ``from google import genai`` must raise ImportError -> RuntimeError
    # (lines 274-275). A package with an empty __path__ and no ``genai`` attr
    # makes the from-import fail the way an uninstalled extra would.
    fake_google = types.ModuleType("google")
    fake_google.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.delitem(sys.modules, "google.genai", raising=False)
    with pytest.raises(RuntimeError, match="gemini"):
        GeminiJudge().evaluate("claim", "real source text")


def test_gemini_judge_parses_mocked_client(monkeypatch):
    class _Resp:
        text = "VERDICT: Uncertain\nREASON: insufficient"

    class _Client:
        def __init__(self, *a, **k):
            pass

        class models:  # noqa: N801
            @staticmethod
            def generate_content(**kwargs):
                assert kwargs["model"] == "gemini-2.0-flash"
                return _Resp()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = _Client  # type: ignore[attr-defined]
    fake_google.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    j = GeminiJudge().evaluate("claim", "real source")
    assert j.verdict is Verdict.UNCERTAIN


def test_gemini_judge_handles_missing_text_attr(monkeypatch):
    # ``getattr(resp, "text", "") or ""`` when the resp has no text.
    class _Resp:
        pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        class models:  # noqa: N801
            @staticmethod
            def generate_content(**kwargs):
                return _Resp()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = _Client  # type: ignore[attr-defined]
    fake_google.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    j = GeminiJudge().evaluate("claim", "real source")
    assert j.verdict is Verdict.PARTIAL  # empty text -> default


# ---------------------------------------------------------------------------
# CLIJudge — argv shapes, empty source, success, timeout, not-found
# ---------------------------------------------------------------------------


def test_cli_judge_empty_source_short_circuits():
    j = CLIJudge().evaluate("claim", "")
    assert j.verdict is Verdict.INACCESSIBLE


@pytest.mark.parametrize(
    "cmd,expected_head",
    [
        ("gemini", ["gemini", "-p"]),
        ("claude", ["claude", "-p"]),
        ("codex", ["codex", "exec"]),
        ("mycli", ["mycli", "-p"]),  # generic fallback
    ],
)
def test_cli_judge_argv_shapes(cmd, expected_head):
    argv = CLIJudge(cmd=cmd)._argv("PROMPT")
    assert argv[: len(expected_head)] == expected_head
    assert argv[-1] == "PROMPT"


def test_cli_judge_success_parses_stdout(monkeypatch):
    class _Proc:
        stdout = "VERDICT: Match\nREASON: ok"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    j = CLIJudge(cmd="gemini").evaluate("claim", "real source")
    assert j.verdict is Verdict.MATCH
    assert "ok" in j.reason


def test_cli_judge_merges_stderr_into_parse(monkeypatch):
    # Verdict only present on stderr -> still parsed (line 324 merge).
    class _Proc:
        stdout = "noise"
        stderr = "VERDICT: Mismatch\nREASON: from stderr"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    j = CLIJudge().evaluate("claim", "real source")
    assert j.verdict is Verdict.MISMATCH


def test_cli_judge_timeout_is_inaccessible(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gemini", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    j = CLIJudge(cmd="gemini").evaluate("claim", "real source")
    assert j.verdict is Verdict.INACCESSIBLE
    assert "timed out" in j.reason


def test_cli_judge_missing_binary_raises_runtime(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="CLI not found"):
        CLIJudge(cmd="gemini").evaluate("claim", "real source")


# ---------------------------------------------------------------------------
# make_judge factory — every branch + unknown spec
# ---------------------------------------------------------------------------


def test_make_judge_keyword():
    assert isinstance(make_judge("keyword"), KeywordJudge)


def test_make_judge_anthropic_default_and_model():
    assert isinstance(make_judge("anthropic"), AnthropicJudge)
    j = make_judge("anthropic:claude-opus-4-1")
    assert isinstance(j, AnthropicJudge)
    assert j.model == "claude-opus-4-1"


def test_make_judge_openai_default_and_model():
    assert isinstance(make_judge("openai"), OpenAIJudge)
    assert make_judge("openai:gpt-4o").model == "gpt-4o"


def test_make_judge_gemini_default_and_model():
    assert isinstance(make_judge("gemini"), GeminiJudge)
    assert make_judge("gemini:gemini-1.5-pro").model == "gemini-1.5-pro"


def test_make_judge_cli_default_and_explicit():
    assert make_judge("cli").cmd == "gemini"  # default cmd
    assert make_judge("cli:codex").cmd == "codex"


def test_make_judge_unknown_spec_raises_value_error():
    with pytest.raises(ValueError, match="unknown judge spec"):
        make_judge("bogus")


# ---------------------------------------------------------------------------
# ensure_judge — availability probe branches (lines 384-393)
# ---------------------------------------------------------------------------


def test_ensure_judge_keyword_always_ok():
    # keyword needs no backend -> returns the judge unconditionally.
    assert isinstance(ensure_judge("keyword"), KeywordJudge)


def test_ensure_judge_sdk_present(monkeypatch):
    # find_spec returns a truthy spec -> no error, judge returned.
    monkeypatch.setattr(judge_mod.importlib.util, "find_spec", lambda name: object())
    assert isinstance(ensure_judge("anthropic"), AnthropicJudge)


def test_ensure_judge_sdk_missing_raises_runtime(monkeypatch):
    monkeypatch.setattr(judge_mod.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="install the 'gemini' extra"):
        ensure_judge("gemini")


def test_ensure_judge_cli_present(monkeypatch):
    monkeypatch.setattr(judge_mod.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    j = ensure_judge("cli:codex")
    assert isinstance(j, CLIJudge)


def test_ensure_judge_cli_missing_raises_runtime(monkeypatch):
    monkeypatch.setattr(judge_mod.shutil, "which", lambda cmd: None)
    with pytest.raises(RuntimeError, match="CLI not found on PATH"):
        ensure_judge("cli:gemini")


def test_ensure_judge_unknown_spec_still_value_error():
    with pytest.raises(ValueError):
        ensure_judge("nope")
