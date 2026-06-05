"""Pluggable LLM judges.

A :class:`Judge` evaluates whether a cited claim is supported by the fetched
source text and returns one of five verdicts (Match / Partial / Mismatch /
Uncertain / Inaccessible) plus a one-line reason.

Concrete judges import their SDK lazily, so each provider is an *optional*
extra. ``KeywordJudge`` is dependency-free and lets the tool run end-to-end
with no API keys (clearly low-confidence). ``CLIJudge`` shells out to a
locally-installed CLI (``gemini`` / ``claude`` / ``codex``), preserving the
multi-CLI cross-check spirit without any framework dependency.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from typing import Protocol, runtime_checkable

from .models import Judgement, Verdict

# ---------------------------------------------------------------------------
# Prompt + parsing
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are fact-checking a citation. Decide whether the CLAIM is supported by the SOURCE.

Reply with EXACTLY one tier on the first line, then a one-line reason:
- Match       claim is explicitly supported by the source
- Partial     partially supported; numbers / year / nuance differ
- Mismatch    absent from, or contradicted by, the source
- Uncertain   the source is present but insufficient to decide (do NOT guess)
- Inaccessible the source text is empty / unusable

Prefer Uncertain over guessing when the source does not clearly settle the claim.

Format strictly:
VERDICT: <Match|Partial|Mismatch|Uncertain|Inaccessible>
REASON: <one line>

CLAIM (with surrounding context):
{claim}

SOURCE (fetched text, may be truncated):
{source}
"""

_VERDICT_WORDS = {
    "match": Verdict.MATCH,
    "partial": Verdict.PARTIAL,
    "mismatch": Verdict.MISMATCH,
    "uncertain": Verdict.UNCERTAIN,
    "inaccessible": Verdict.INACCESSIBLE,
}


def build_prompt(claim_context: str, source_text: str) -> str:
    return PROMPT_TEMPLATE.format(
        claim=claim_context.strip()[:2000],
        source=(source_text or "").strip()[:8000],
    )


def parse_response(text: str) -> tuple[Verdict, str]:
    """Parse an LLM reply into (verdict, reason). Robust to loose formatting.

    An explicit ``VERDICT: <word>`` line takes precedence over any verdict word
    appearing earlier in free-form prose, so that reasoning written before the
    structured line cannot flip the verdict (e.g. "This is a partial match at
    best.\\nVERDICT: Mismatch"). Only when no explicit VERDICT line is present
    do we fall back to the first verdict word found anywhere.
    """
    lines = text.splitlines()

    def _trailing_reason(start: int) -> str:
        for line in lines[start:]:
            s = line.strip()
            if s.lower().startswith("reason"):
                return re.sub(r"^reason[:\s-]*", "", s, flags=re.I).strip()
        return ""

    # Pass 1: an explicit "VERDICT: <word>" line wins, wherever it appears.
    for i, line in enumerate(lines):
        m = re.match(
            r"[:\s\-*#>]*verdict[:\s-]+(match|partial|mismatch|uncertain|inaccessible)\b",
            line.strip(),
            flags=re.I,
        )
        if m:
            verdict = _VERDICT_WORDS[m.group(1).lower()]
            rest = re.sub(
                r"^[:\s\-*#>]*verdict[:\s-]+\w+[:\s-]*", "", line.strip(), flags=re.I
            ).strip()
            reason = rest or _trailing_reason(i + 1)
            return verdict, reason[:300]

    # Pass 2 (fallback): first verdict word anywhere in the reply.
    verdict = Verdict.PARTIAL
    reason = ""
    found = False
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if not found:
            m = re.search(r"\b(match|partial|mismatch|uncertain|inaccessible)\b", low)
            if m:
                verdict = _VERDICT_WORDS[m.group(1)]
                found = True
                # If reason is on the same line after the verdict word, keep it.
                rest = re.sub(r"^[:\s-]*verdict[:\s-]*", "", stripped, flags=re.I)
                rest = re.sub(r"\b(match|partial|mismatch|uncertain|inaccessible)\b[:\s-]*", "", rest, count=1, flags=re.I)
                if rest.strip():
                    reason = rest.strip()
                continue
        if low.startswith("reason"):
            reason = re.sub(r"^reason[:\s-]*", "", stripped, flags=re.I).strip()
            break
        if found and not reason and stripped:
            reason = stripped
            break
    return verdict, reason[:300]


# ---------------------------------------------------------------------------
# Judge protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Judge(Protocol):
    name: str

    def evaluate(self, claim_context: str, source_text: str) -> Judgement: ...


def _empty_source(source_text: str) -> bool:
    return not source_text or not source_text.strip()


# ---------------------------------------------------------------------------
# Keyword (dependency-free) judge
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "by", "at", "as", "that", "this",
    "from", "it", "its", "we", "our", "their", "they",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2 and t.lower() not in _STOP}


# Numeric tokens carry the quantitative substance of most citations (a year, a
# percentage, an N). The token-overlap heuristic ignores them, so a claim can
# reach high word overlap while its key figure is absent from the source.
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numeric_tokens(text: str) -> set[str]:
    """Years and numbers in ``text``, normalized (commas stripped, ``.0`` kept)."""
    return {m.group(0).replace(",", "") for m in _NUM_RE.finditer(text)}


class KeywordJudge:
    """Token-overlap heuristic. No dependencies, clearly low-confidence."""

    name = "keyword"

    def evaluate(self, claim_context: str, source_text: str) -> Judgement:
        if _empty_source(source_text):
            return Judgement(self.name, Verdict.INACCESSIBLE, "empty source text")
        claim_tok = _tokens(claim_context)
        src_tok = _tokens(source_text)
        if not claim_tok:
            return Judgement(self.name, Verdict.PARTIAL, "no content tokens in claim")
        overlap = len(claim_tok & src_tok) / len(claim_tok)
        if overlap >= 0.5:
            v = Verdict.MATCH
        elif overlap >= 0.2:
            v = Verdict.PARTIAL
        else:
            v = Verdict.MISMATCH
        reason = f"token overlap {overlap:.0%} (heuristic, low confidence)"

        # P1-5: a keyword Match must not assert quantitative agreement it cannot
        # check. If the claim carries year/number tokens that the source does not
        # contain, the cited figure is unverified — clamp Match down to Partial
        # rather than awarding a full 50-pt claim_match on word overlap alone.
        if v is Verdict.MATCH:
            claim_nums = _numeric_tokens(claim_context)
            if claim_nums and not (claim_nums <= _numeric_tokens(source_text)):
                missing = sorted(claim_nums - _numeric_tokens(source_text))
                v = Verdict.PARTIAL
                reason = (
                    f"token overlap {overlap:.0%} but claim figure(s) "
                    f"{', '.join(missing)} absent from source (heuristic clamp)"
                )

        return Judgement(self.name, v, reason)


# ---------------------------------------------------------------------------
# SDK-backed judges (lazy imports => optional extras)
# ---------------------------------------------------------------------------


class AnthropicJudge:
    """Judge backed by the ``anthropic`` SDK + ``ANTHROPIC_API_KEY``."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model
        self.name = f"anthropic:{model}"

    def evaluate(self, claim_context: str, source_text: str) -> Judgement:
        if _empty_source(source_text):
            return Judgement(self.name, Verdict.INACCESSIBLE, "empty source text")
        try:
            import anthropic  # noqa: PLC0415  (lazy optional import)
        except ImportError as exc:
            raise RuntimeError("install the 'anthropic' extra: pip install paper-verify[anthropic]") from exc
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{"role": "user", "content": build_prompt(claim_context, source_text)}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        v, reason = parse_response(text)
        return Judgement(self.name, v, reason)


class OpenAIJudge:
    """Judge backed by the ``openai`` SDK + ``OPENAI_API_KEY``."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self.name = f"openai:{model}"

    def evaluate(self, claim_context: str, source_text: str) -> Judgement:
        if _empty_source(source_text):
            return Judgement(self.name, Verdict.INACCESSIBLE, "empty source text")
        try:
            import openai  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("install the 'openai' extra: pip install paper-verify[openai]") from exc
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=200,
            messages=[{"role": "user", "content": build_prompt(claim_context, source_text)}],
        )
        text = resp.choices[0].message.content or ""
        v, reason = parse_response(text)
        return Judgement(self.name, v, reason)


class GeminiJudge:
    """Judge backed by ``google-genai`` + ``GEMINI_API_KEY``."""

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        self.model = model
        self.name = f"gemini:{model}"

    def evaluate(self, claim_context: str, source_text: str) -> Judgement:
        if _empty_source(source_text):
            return Judgement(self.name, Verdict.INACCESSIBLE, "empty source text")
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("install the 'gemini' extra: pip install paper-verify[gemini]") from exc
        client = genai.Client()
        resp = client.models.generate_content(
            model=self.model,
            contents=build_prompt(claim_context, source_text),
        )
        text = getattr(resp, "text", "") or ""
        v, reason = parse_response(text)
        return Judgement(self.name, v, reason)


class CLIJudge:
    """Judge that shells out to a locally-installed CLI.

    Supports ``gemini``, ``claude``, and ``codex`` invocation conventions.
    Preserves the original multi-CLI cross-check spirit with zero framework
    or SDK dependency — it just needs the CLI on ``$PATH``.
    """

    def __init__(self, cmd: str = "gemini", timeout: int = 90) -> None:
        self.cmd = cmd
        self.timeout = timeout
        self.name = f"cli:{cmd}"

    def _argv(self, prompt: str) -> list[str]:
        if self.cmd == "gemini":
            return ["gemini", "-p", prompt]
        if self.cmd == "claude":
            return ["claude", "-p", prompt]
        if self.cmd == "codex":
            return ["codex", "exec", prompt]
        # generic fallback: assume `<cmd> -p <prompt>`
        return [self.cmd, "-p", prompt]

    def evaluate(self, claim_context: str, source_text: str) -> Judgement:
        if _empty_source(source_text):
            return Judgement(self.name, Verdict.INACCESSIBLE, "empty source text")
        prompt = build_prompt(claim_context, source_text)
        try:
            proc = subprocess.run(
                self._argv(prompt),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"CLI not found on PATH: {self.cmd}") from exc
        except subprocess.TimeoutExpired:
            return Judgement(self.name, Verdict.INACCESSIBLE, f"{self.cmd} CLI timed out")
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        v, reason = parse_response(out)
        return Judgement(self.name, v, reason)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_judge(spec: str) -> Judge:
    """Build a judge from a spec string.

    Examples:
        ``"keyword"``                       -> KeywordJudge
        ``"anthropic"`` / ``"anthropic:claude-sonnet-4-6"``
        ``"openai"`` / ``"openai:gpt-4o-mini"``
        ``"gemini"`` / ``"gemini:gemini-2.0-flash"``
        ``"cli:gemini"`` / ``"cli:claude"`` / ``"cli:codex"``
    """
    spec = spec.strip()
    provider, _, rest = spec.partition(":")
    provider = provider.lower()
    rest = rest.strip()

    if provider == "keyword":
        return KeywordJudge()
    if provider == "anthropic":
        return AnthropicJudge(model=rest) if rest else AnthropicJudge()
    if provider == "openai":
        return OpenAIJudge(model=rest) if rest else OpenAIJudge()
    if provider == "gemini":
        return GeminiJudge(model=rest) if rest else GeminiJudge()
    if provider == "cli":
        return CLIJudge(cmd=rest or "gemini")
    raise ValueError(
        f"unknown judge spec: {spec!r} "
        "(expected keyword | anthropic[:model] | openai[:model] | gemini[:model] | cli:<gemini|claude|codex>)"
    )


# Maps a judge provider to the import name of the SDK that its ``.evaluate``
# lazily imports. Used by :func:`ensure_judge` to probe availability without a
# network call. ``keyword`` needs nothing; ``cli`` needs the CLI on ``$PATH``.
_PROVIDER_MODULE = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google.genai",
}


def ensure_judge(spec: str) -> Judge:
    """Build a judge and verify its backend is available, offline.

    Like :func:`make_judge`, but raises ``RuntimeError`` (the same exception
    type ``.evaluate`` raises for a missing extra / missing CLI) when the judge
    could be constructed but its SDK is not installed or its CLI is not on
    ``$PATH``. No network call or API key is needed — this only probes that the
    backend *could* run. ``ValueError`` still signals an unknown spec shape.
    """
    judge = make_judge(spec)
    provider = spec.strip().partition(":")[0].lower()
    if provider in _PROVIDER_MODULE:
        if importlib.util.find_spec(_PROVIDER_MODULE[provider]) is None:
            raise RuntimeError(f"install the '{provider}' extra: pip install paper-verify[{provider}]")
    elif provider == "cli":
        cmd = getattr(judge, "cmd", "gemini")
        if shutil.which(cmd) is None:
            raise RuntimeError(f"CLI not found on PATH: {cmd}")
    return judge
