"""Score externally-gathered evidence with the standard rubric.

An external agent (a web chat, another harness) does fetch + judging with its
own tools, writes an *evidence JSON* (see ``examples/evidence-sample.json``),
and this module converts it to the shared dataclasses and reuses
``score_citation`` + ``Report`` — so external results get exactly the same
100-point rubric as the CLI pipeline. No new scoring logic lives here.
"""

from __future__ import annotations

from .models import Citation, Fetched, Judgement, Report, Verdict
from .score import score_citation

VALID_LEVELS = {"L1", "L2", "L3"}

_VERDICT_NAMES = " | ".join(v.value for v in Verdict)


class EvidenceError(ValueError):
    """Evidence JSON is malformed; the message names the offending citation index."""


def report_from_evidence(data: dict) -> Report:
    """Convert an evidence dict to a scored :class:`Report`.

    Tolerant on optional fields (missing ids are assigned 1..N, missing
    ``fetched`` fields take the dataclass defaults); strict with clear,
    index-named errors on anything that would otherwise be guessed.
    """
    if not isinstance(data, dict):
        raise EvidenceError("evidence root must be a JSON object")
    level = str(data.get("level", "L2")).upper()
    if level not in VALID_LEVELS:
        raise EvidenceError(f"unknown level: {level!r} (expected L1 | L2 | L3)")
    items = data.get("citations")
    if not isinstance(items, list):
        raise EvidenceError("'citations' must be a list")

    scored = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise EvidenceError(f"citations[{i}] must be an object")

        cd = item.get("citation")
        if not isinstance(cd, dict):
            raise EvidenceError(f"citations[{i}].citation must be an object")
        cd = dict(cd)
        cd.setdefault("id", i + 1)
        cd.setdefault("type", "URL")
        if not cd.get("ref"):
            raise EvidenceError(f"citations[{i}].citation.ref is required")
        try:
            citation = Citation.from_dict(cd)
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError(f"citations[{i}].citation invalid: {exc}") from None

        fd = item.get("fetched")
        fetched = None
        if fd is not None:
            if not isinstance(fd, dict):
                raise EvidenceError(f"citations[{i}].fetched must be an object or null")
            fd = dict(fd)
            fd.setdefault("id", citation.id)
            try:
                fetched = Fetched.from_dict(fd)
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceError(f"citations[{i}].fetched invalid: {exc}") from None

        judgements = []
        for k, jd in enumerate(item.get("judgements") or []):
            if not isinstance(jd, dict):
                raise EvidenceError(f"citations[{i}].judgements[{k}] must be an object")
            raw = jd.get("verdict")
            if raw is None:
                raise EvidenceError(f"citations[{i}].judgements[{k}].verdict is required")
            try:
                verdict = Verdict(str(raw))
            except ValueError:
                raise EvidenceError(
                    f"citations[{i}].judgements[{k}].verdict {raw!r} unknown "
                    f"(expected {_VERDICT_NAMES})"
                ) from None
            judgements.append(
                Judgement(
                    judge=str(jd.get("judge", "external")),
                    verdict=verdict,
                    reason=str(jd.get("reason", "")),
                )
            )

        scored.append(score_citation(citation, fetched, judgements, level=level))

    judges = sorted({j.judge for sc in scored for j in sc.judgements})
    return Report(
        source_file=str(data.get("source_file") or "<evidence>"),
        level=level,
        scored=scored,
        judges=judges,
        profile=data.get("profile"),
    )
