"""Behavior coverage for ``paperverify.cli``.

Network-free: ``fetch_all`` and the judge factory are monkeypatched, so the
pipeline runs with no HTTP / API keys. Covers the branches the existing suite
left uncovered: ``run_pipeline`` level validation + log lines + the tie-break
path (success and judge-failure), ``_resolve_profile_judges`` availability
selection / fallback, and ``main()`` dispatch — list-profiles, missing-file,
bad level, bad profile, file-not-found, unicode-fallback read, --json vs file
output, the tier-F warning line, and exit codes.

Intentionally NOT covered (entrypoint): the ``if __name__ == "__main__"`` guard
on the last line — it only re-dispatches ``main()`` under ``python -m`` and is
exercised indirectly by the subprocess tests, not unit-callable.
"""

import json
import sys

import pytest

from paperverify import cli
from paperverify.models import Fetched, Verdict
from paperverify.report import render_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_fetch_all(status=200, title="Smith 2017", abstract="In 2017 Smith reported a gain."):
    def inner(citations, level="L2", workers=4):
        return {
            c.id: Fetched(id=c.id, status=status, title=title, abstract=abstract)
            for c in citations
        }

    return inner


class _StubJudge:
    def __init__(self, name, verdict):
        self.name = name
        self._verdict = verdict

    def evaluate(self, claim_context, source_text):
        from paperverify.models import Judgement

        return Judgement(self.name, self._verdict, "stub")


# ---------------------------------------------------------------------------
# run_pipeline — level validation + log lines
# ---------------------------------------------------------------------------


def test_run_pipeline_rejects_unknown_level():
    with pytest.raises(ValueError, match="unknown level"):
        cli.run_pipeline("text", level="L9")


def test_run_pipeline_log_no_citations(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    cli.run_pipeline("Just prose with no citations.", level="L1", log=True)
    err = capsys.readouterr().err
    assert "No citations found." in err


def test_run_pipeline_log_progress_line(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    cli.run_pipeline(
        "Smith 2017 https://example.com/ref",
        level="L2",
        judge_specs=["keyword"],
        log=True,
    )
    err = capsys.readouterr().err
    assert "paper-verify:" in err
    assert "citations, level L2" in err
    assert "judges: keyword" in err


def test_run_pipeline_log_progress_line_no_judges_at_l1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    cli.run_pipeline("Smith 2017 https://example.com/ref", level="L1", log=True)
    err = capsys.readouterr().err
    assert "judges: (none)" in err


def test_run_pipeline_log_judge_failure_line(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())

    class Broken:
        name = "broken"

        def evaluate(self, claim_context, source_text):
            raise RuntimeError("kaboom-detail")

    monkeypatch.setattr(cli, "make_judge", lambda spec: Broken())
    report = cli.run_pipeline(
        "Smith 2017 https://example.com/ref", judge_specs=["broken"], log=True
    )
    err = capsys.readouterr().err
    assert "judge broken failed: kaboom-detail" in err
    # The failure is still recorded as an INACCESSIBLE judgement.
    item = render_json(report)["citations"][0]
    assert item["judgements"][0]["verdict"] == "Inaccessible"


# ---------------------------------------------------------------------------
# run_pipeline — tie-break path
# ---------------------------------------------------------------------------


def test_run_pipeline_tiebreak_resolves_split(monkeypatch):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    judges = {
        "a": _StubJudge("a", Verdict.MATCH),
        "b": _StubJudge("b", Verdict.MISMATCH),
        "t": _StubJudge("t", Verdict.MATCH),
    }
    monkeypatch.setattr(cli, "make_judge", lambda spec: judges[spec])

    report = cli.run_pipeline(
        "Smith 2017 https://example.com/ref",
        judge_specs=["a", "b"],
        tiebreak_spec="t",
    )
    item = render_json(report)["citations"][0]
    # Tie-break arbiter (Match) resolves the a/b split to Match.
    assert item["effective_verdict"] == "Match"


def test_run_pipeline_tiebreak_not_invoked_on_consensus(monkeypatch):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    invoked = {"t": 0}

    class Tie:
        name = "t"

        def evaluate(self, claim_context, source_text):
            invoked["t"] += 1
            from paperverify.models import Judgement

            return Judgement("t", Verdict.MATCH, "x")

    judges = {"a": _StubJudge("a", Verdict.MATCH), "b": _StubJudge("b", Verdict.MATCH), "t": Tie()}
    monkeypatch.setattr(cli, "make_judge", lambda spec: judges[spec])

    cli.run_pipeline(
        "Smith 2017 https://example.com/ref",
        judge_specs=["a", "b"],
        tiebreak_spec="t",
    )
    # a and b already agree -> tie-break judge is never called.
    assert invoked["t"] == 0


def test_run_pipeline_tiebreak_judge_failure_logged(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())

    class BoomTie:
        name = "t"

        def evaluate(self, claim_context, source_text):
            raise RuntimeError("tiebreak-broke")

    judges = {
        "a": _StubJudge("a", Verdict.MATCH),
        "b": _StubJudge("b", Verdict.MISMATCH),
        "t": BoomTie(),
    }
    monkeypatch.setattr(cli, "make_judge", lambda spec: judges[spec])

    cli.run_pipeline(
        "Smith 2017 https://example.com/ref",
        judge_specs=["a", "b"],
        tiebreak_spec="t",
        log=True,
    )
    err = capsys.readouterr().err
    assert "tie-break t failed: tiebreak-broke" in err


# ---------------------------------------------------------------------------
# _build_parser — flag wiring
# ---------------------------------------------------------------------------


def test_build_parser_defaults():
    args = cli._build_parser().parse_args(["doc.md"])
    assert args.file == "doc.md"
    assert args.level == "L2"
    assert args.judge is None
    assert args.workers == 4
    assert args.json is False
    assert args.profile is None
    assert args.tiebreak is None


def test_build_parser_repeatable_judge_and_flags():
    args = cli._build_parser().parse_args(
        ["doc.md", "--judge", "keyword", "--judge", "gemini",
         "--level", "L3", "--workers", "8", "--json", "--tiebreak", "cli:gemini"]
    )
    assert args.judge == ["keyword", "gemini"]
    assert args.level == "L3"
    assert args.workers == 8
    assert args.json is True
    assert args.tiebreak == "cli:gemini"


# ---------------------------------------------------------------------------
# _resolve_profile_judges — availability filtering and fallback
# ---------------------------------------------------------------------------


def test_resolve_profile_judges_keeps_available(monkeypatch):
    seen = []

    def fake_ensure(spec):
        seen.append(spec)
        if spec == "anthropic":
            raise RuntimeError("no anthropic sdk")
        return object()

    monkeypatch.setattr(cli, "ensure_judge", fake_ensure)
    out = cli._resolve_profile_judges(("anthropic", "keyword"))
    # anthropic dropped (unavailable), keyword kept.
    assert out == ["keyword"]
    assert seen == ["anthropic", "keyword"]


def test_resolve_profile_judges_falls_back_to_keyword_when_none(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "ensure_judge",
        lambda spec: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    out = cli._resolve_profile_judges(("anthropic", "gemini"))
    assert out == ["keyword"]
    assert "none of the profile's recommended judges are available" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() — dispatch, exit codes, output formats
# ---------------------------------------------------------------------------


def test_main_list_profiles_exit0(capsys):
    rc = cli.main(["--list-profiles"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert {entry["key"] for entry in data} == {"claude-code", "cursor", "codex", "gemini"}


def test_main_missing_file_returns_2(capsys):
    rc = cli.main([])
    assert rc == 2
    assert "a file argument is required" in capsys.readouterr().err


def test_main_file_not_found_returns_2(capsys, tmp_path):
    rc = cli.main([str(tmp_path / "does_not_exist.md")])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err


def test_main_unknown_profile_returns_2(capsys, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("prose", encoding="utf-8")
    rc = cli.main([str(src), "--profile", "no-such-profile"])
    assert rc == 2
    assert "unknown harness profile" in capsys.readouterr().err


def test_main_bad_level_via_pipeline_returns_2(monkeypatch, capsys, tmp_path):
    # argparse restricts --level, so force the ValueError from run_pipeline by
    # stubbing it to raise (simulates a bad spec reaching the pipeline).
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")

    def boom(*a, **k):
        raise ValueError("unknown level: 'LX'")

    monkeypatch.setattr(cli, "run_pipeline", boom)
    rc = cli.main([str(src), "--level", "L1"])
    assert rc == 2
    assert "error: unknown level" in capsys.readouterr().err


def test_main_writes_md_and_jsonl_in_default_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")
    out_dir = tmp_path / "out"
    rc = cli.main([str(src), "--level", "L2", "--judge", "keyword", "--out", str(out_dir)])
    assert rc == 0
    assert (out_dir / "doc_report.md").is_file()
    assert (out_dir / "doc_claims.jsonl").is_file()
    out = capsys.readouterr().out
    assert "Overall:" in out
    assert "Report:" in out
    assert "Claims:" in out


def test_main_default_mode_no_out_writes_to_cwd(monkeypatch, capsys, tmp_path):
    # Without --out and not in JSON mode, output defaults to the current dir (".").
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")
    rc = cli.main([str(src), "--level", "L2", "--judge", "keyword"])
    assert rc == 0
    # Files land in the cwd (".") since --out was omitted.
    assert (tmp_path / "doc_report.md").is_file()
    assert (tmp_path / "doc_claims.jsonl").is_file()


def test_main_json_mode_no_out_writes_no_files(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")
    rc = cli.main([str(src), "--level", "L2", "--judge", "keyword", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    # JSON on stdout, human summary on stderr.
    data = json.loads(captured.out)
    assert data["level"] == "L2"
    assert "Overall:" in captured.err
    # No files written without --out in JSON mode.
    assert not (tmp_path / "doc_report.md").exists()


def test_main_warns_on_tier_f(monkeypatch, capsys, tmp_path):
    # 404 + mismatch verdict -> tier F citation -> the warning line fires.
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all(status=404, title="", abstract=""))
    monkeypatch.setattr(cli, "make_judge", lambda spec: _StubJudge(spec, Verdict.MISMATCH))
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")
    rc = cli.main([str(src), "--level", "L2", "--judge", "keyword", "--out", str(tmp_path)])
    assert rc == 0
    assert "tier-F citations" in capsys.readouterr().out


def test_main_profile_fills_default_judges(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    # Force the profile resolver to choose keyword so no SDK is needed.
    monkeypatch.setattr(cli, "_resolve_profile_judges", lambda rec: ["keyword"])
    src = tmp_path / "doc.md"
    src.write_text("Smith 2017 https://example.com/ref", encoding="utf-8")
    rc = cli.main([str(src), "--profile", "claude-code", "--level", "L2", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["profile"] == "claude-code"
    assert data["judges"] == ["keyword"]


def test_main_unicode_decode_error_falls_back_to_replace(monkeypatch, capsys, tmp_path):
    # A file that is not valid UTF-8 must still be read (errors='replace'),
    # not crash the run.
    monkeypatch.setattr(cli, "fetch_all", _fake_fetch_all())
    src = tmp_path / "doc.md"
    src.write_bytes(b"Smith 2017 \xff\xfe https://example.com/ref")
    rc = cli.main([str(src), "--level", "L2", "--judge", "keyword", "--json"])
    assert rc == 0
    json.loads(capsys.readouterr().out)  # ran to completion, valid JSON


# ---------------------------------------------------------------------------
# Entrypoint smoke (covers `python -m paperverify.cli` dispatch)
# ---------------------------------------------------------------------------


def test_cli_module_runs_as_main(tmp_path):
    import subprocess

    src = tmp_path / "doc.md"
    src.write_text("No citations here.", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "paperverify.cli", str(src), "--level", "L1", "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["level"] == "L1"
