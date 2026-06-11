"""Command-line entrypoint for paper-verify.

Pipeline: extract -> fetch_all -> judge (all judges) -> score -> Report ->
write ``<out>/<basename>_report.md`` and ``<basename>_claims.jsonl``.

Defaults are chosen so the tool runs with **no API keys and no network LLM**:
level ``L2`` and judge ``keyword``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import extract
from .fetch import fetch_all
from .harness import get_profile, list_profiles
from .judge import ensure_judge, make_judge
from .models import Judgement, Report, Tier, Verdict
from .offline import report_from_evidence
from .report import render_json, render_markdown
from .score import score_citation

VALID_LEVELS = {"L1", "L2", "L3"}


def run_pipeline(
    text: str,
    *,
    source_file: str = "<text>",
    level: str = "L2",
    judge_specs: list[str] | None = None,
    workers: int = 4,
    log: bool = False,
    profile: str | None = None,
    tiebreak_spec: str | None = None,
) -> Report:
    """Run the full extract -> fetch -> judge -> score pipeline on ``text``.

    Shared by the CLI and the MCP server so the pipeline logic lives in one
    place. Returns a :class:`Report`. Raises ``ValueError`` on a bad judge spec.

    Args:
        text: full document contents.
        source_file: label recorded in the report (path or ``"<text>"``).
        level: "L1" | "L2" | "L3".
        judge_specs: judge spec strings (default ``["keyword"]``).
        workers: parallel fetch workers.
        log: if True, emit human progress lines to stderr.
        profile: active harness profile key recorded in the report (or None).
        tiebreak_spec: optional judge spec used ONLY to break a tie when 2+
            primary judges disagree on a citation (default None = no tie-break).
    """
    level = level.upper()
    if level not in VALID_LEVELS:
        raise ValueError(f"unknown level: {level!r} (expected L1 | L2 | L3)")

    citations = extract(text)
    if log and not citations:
        print("No citations found.", file=sys.stderr)

    specs = judge_specs or ["keyword"]
    judges = [make_judge(s) for s in specs] if level != "L1" else []
    tiebreak_judge = (
        make_judge(tiebreak_spec) if (tiebreak_spec and level != "L1") else None
    )

    if log:
        print(
            f"paper-verify: {len(citations)} citations, level {level}, "
            f"judges: {', '.join(j.name for j in judges) if judges else '(none)'}",
            file=sys.stderr,
        )

    fetched = fetch_all(citations, level=level, workers=workers)

    scored = []
    for c in citations:
        f = fetched.get(c.id)
        judgements = []
        tiebreak_judgement = None
        if level != "L1":
            source = f"{f.title}\n{f.abstract}" if f else ""
            for judge in judges:
                try:
                    judgements.append(judge.evaluate(c.context, source))
                except Exception as exc:
                    reason = str(exc)[:300]
                    if log:
                        print(f"  judge {judge.name} failed: {reason}", file=sys.stderr)
                    judgements.append(
                        Judgement(judge.name, Verdict.INACCESSIBLE, f"judge failed: {reason}")
                    )
            # Tie-break only when 2+ judges disagree — keeps it cheap (one extra
            # call only for genuinely split citations).
            if (
                tiebreak_judge is not None
                and len(judgements) >= 2
                and len({j.verdict for j in judgements}) > 1
            ):
                try:
                    tiebreak_judgement = tiebreak_judge.evaluate(c.context, source)
                except Exception as exc:
                    reason = str(exc)[:300]
                    if log:
                        print(f"  tie-break {tiebreak_judge.name} failed: {reason}", file=sys.stderr)
                    tiebreak_judgement = None
        scored.append(
            score_citation(c, f, judgements, level=level, tiebreak_judgement=tiebreak_judgement)
        )

    used_judge_names = sorted({j.judge for sc in scored for j in sc.judgements})
    return Report(
        source_file=source_file,
        level=level,
        scored=scored,
        judges=used_judge_names or ([] if level == "L1" else [j.name for j in judges]),
        profile=profile,
    )


def _emit(report: Report, args, *, base: str, as_json: bool, human) -> int:
    """Write files / JSON / human summary for a finished report (shared tail)."""
    out = args.out
    if out is None and not as_json:
        out = "."
    if out is not None:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / f"{base}_report.md"
        claims_path = out_dir / f"{base}_claims.jsonl"
        report_path.write_text(render_markdown(report), encoding="utf-8")
        claims_path.write_text(
            report.to_jsonl() + ("\n" if report.scored else ""), encoding="utf-8"
        )
    else:
        report_path = claims_path = None

    if as_json:
        print(json.dumps(render_json(report), ensure_ascii=False))

    dist = report.tier_distribution()
    summary = " ".join(
        f"{t.symbol}{t.value}:{dist.get(t, 0)}" for t in [Tier.A, Tier.B, Tier.C, Tier.F]
    )
    print(
        f"Overall: {report.overall_score}/100 "
        f"{report.overall_tier.symbol} {report.overall_tier.value}  [{summary}]",
        file=human,
    )
    if report.has_failure:
        print("⚠️  Document contains tier-F citations — see report.", file=human)
    if report_path is not None:
        print(f"Report:  {report_path}", file=human)
        print(f"Claims:  {claims_path}", file=human)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper-verify",
        description="Citation fact-checking: extract citations, fetch sources, "
        "judge claim/source match, score on a 100-point rubric, emit a report.",
    )
    # ``file`` is optional so ``--list-profiles`` can run without it; main()
    # enforces that ``file`` is present for an actual verification run.
    p.add_argument("file", nargs="?", help="Markdown / text file to verify")
    p.add_argument(
        "--profile",
        default=None,
        metavar="KEY",
        help="harness profile (claude-code | cursor | codex | gemini, aliases ok). "
        "When set and no explicit --judge is given, defaults judges to the "
        "profile's recommended judges (first available); explicit --judge wins.",
    )
    p.add_argument(
        "--list-profiles",
        action="store_true",
        help="print all harness profiles as JSON to stdout and exit (no file needed)",
    )
    p.add_argument(
        "--extract-only",
        action="store_true",
        help="extract citations and print them as JSON to stdout — no network, "
        "no LLM (agent surface; pairs with --from-evidence)",
    )
    p.add_argument(
        "--from-evidence",
        default=None,
        metavar="FILE",
        help="skip extract/fetch/judge and score an externally-gathered evidence "
        "JSON file with the standard rubric (see docs/webchat/). Omit the "
        "positional file argument in this mode.",
    )
    p.add_argument(
        "--level",
        choices=["L1", "L2", "L3"],
        default="L2",
        help="L1=HTTP status only, L2=abstract/title match (default), L3=full content",
    )
    p.add_argument(
        "--judge",
        action="append",
        default=None,
        metavar="SPEC",
        help="judge spec (repeatable for cross-check), e.g. keyword, "
        "anthropic:claude-sonnet-4-6, gemini, cli:gemini. Default: keyword",
    )
    p.add_argument(
        "--tiebreak",
        default=None,
        metavar="SPEC",
        help="optional 3rd judge spec used ONLY to break a tie when 2+ --judge "
        "judges disagree on a citation. On a genuine consensus (incl. after "
        "tie-break) the cross-check 10 pts are awarded; split with no tie-break "
        "marks the citation Uncertain. Default: no tie-break.",
    )
    p.add_argument("--out", default=None, help="output directory for .md/.jsonl files "
                   "(default: current dir; optional when --json is set)")
    p.add_argument("--workers", type=int, default=4, help="parallel fetch workers (default: 4)")
    p.add_argument(
        "--json",
        action="store_true",
        help="emit the full structured result as JSON to stdout (human summary "
        "goes to stderr). Additive: pass --out to also write .md/.jsonl files.",
    )
    return p


def _resolve_profile_judges(recommended: tuple[str, ...]) -> list[str]:
    """Pick the profile's recommended judges that are actually available.

    Tries each spec in order via :func:`ensure_judge`, skipping any whose SDK /
    CLI is missing (``RuntimeError``). Returns the available specs, or
    ``["keyword"]`` with a one-line stderr note when none are available.
    """
    available = []
    for spec in recommended:
        try:
            ensure_judge(spec)
        except RuntimeError:
            continue
        available.append(spec)
    if available:
        return available
    print(
        "paper-verify: none of the profile's recommended judges are available; "
        "falling back to 'keyword'.",
        file=sys.stderr,
    )
    return ["keyword"]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Exit codes (stable for agents):
        0  ran successfully — regardless of grades / tier-F findings.
        2  real error — file not found, unreadable, or bad judge / profile spec.
    """
    args = _build_parser().parse_args(argv)

    # --list-profiles: print all profiles as JSON and exit, no file needed.
    if args.list_profiles:
        print(json.dumps([p.to_dict() for p in list_profiles()], ensure_ascii=False))
        return 0

    as_json = args.json
    # In --json mode the human summary goes to stderr so stdout stays pure JSON.
    human = sys.stderr if as_json else sys.stdout

    # --from-evidence: score externally-gathered evidence; no positional file.
    if args.from_evidence:
        if args.file:
            print(
                "error: pass either a file or --from-evidence, not both",
                file=sys.stderr,
            )
            return 2
        ev_path = Path(args.from_evidence)
        if not ev_path.is_file():
            print(f"error: file not found: {ev_path}", file=sys.stderr)
            return 2
        try:
            data = json.loads(ev_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: invalid JSON in {ev_path}: {exc}", file=sys.stderr)
            return 2
        try:
            report = report_from_evidence(data)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _emit(report, args, base=ev_path.stem, as_json=as_json, human=human)

    if not args.file:
        print("error: a file argument is required (or use --list-profiles)", file=sys.stderr)
        return 2

    # Resolve the harness profile (if any) before reading the file so an unknown
    # key fails fast with the clear error message.
    profile_key: str | None = None
    profile_judges: list[str] | None = None
    if args.profile is not None:
        try:
            prof = get_profile(args.profile)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        profile_key = prof.key
        # Explicit --judge always wins; profile only fills in defaults.
        if not args.judge:
            profile_judges = _resolve_profile_judges(prof.recommended_judges)

    src = Path(args.file)
    if not src.is_file():
        print(f"error: file not found: {src}", file=sys.stderr)
        return 2

    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = src.read_text(encoding="utf-8", errors="replace")

    if args.extract_only:
        cites = extract(text)
        print(json.dumps({"citations": [c.to_dict() for c in cites]}, ensure_ascii=False))
        return 0

    judge_specs = args.judge or profile_judges or ["keyword"]
    try:
        report = run_pipeline(
            text,
            source_file=str(src),
            level=args.level,
            judge_specs=judge_specs,
            workers=args.workers,
            log=True,
            profile=profile_key,
            tiebreak_spec=args.tiebreak,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return _emit(report, args, base=src.stem, as_json=as_json, human=human)


if __name__ == "__main__":
    raise SystemExit(main())
