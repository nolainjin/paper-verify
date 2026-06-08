"""Tests for the machine-readable JSON surface — no network, no API keys."""

import json
import subprocess
import sys

from paperverify import cli
from paperverify.models import Citation, Fetched, Judgement, Report, Verdict
from paperverify.report import SCHEMA_VERSION, render_json
from paperverify.score import score_citation

_TOP_LEVEL_KEYS = {
    "schema_version",
    "source_file",
    "level",
    "profile",
    "judges",
    "overall_score",
    "overall_tier",
    "has_failure",
    "keyword_only",
    "tier_distribution",
    "citations",
}


def _report() -> Report:
    c = Citation(id=1, type="URL", ref="https://x.com", context="Smith 2017 gain.", line=1)
    f = Fetched(id=1, status=200, title="Smith 2017", abstract="In 2017 Smith reported a gain.")
    sc = score_citation(c, f, [Judgement("keyword", Verdict.MATCH, "r")])
    return Report(source_file="doc.md", level="L2", scored=[sc], judges=["keyword"])


def test_render_json_has_documented_keys():
    out = render_json(_report())
    assert set(out.keys()) == _TOP_LEVEL_KEYS
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["source_file"] == "doc.md"
    assert out["level"] == "L2"
    assert out["profile"] is None
    assert out["judges"] == ["keyword"]
    assert isinstance(out["overall_score"], (int, float))
    assert out["overall_tier"] in {"A", "B", "C", "F"}
    assert isinstance(out["has_failure"], bool)


def test_render_json_tier_distribution_shape():
    out = render_json(_report())
    dist = out["tier_distribution"]
    assert set(dist.keys()) == {"A", "B", "C", "F"}
    assert all(isinstance(v, int) for v in dist.values())
    assert sum(dist.values()) == len(out["citations"])


def test_render_json_citation_item_shape():
    out = render_json(_report())
    assert len(out["citations"]) == 1
    item = out["citations"][0]
    assert set(item.keys()) == {
        "citation",
        "fetched",
        "judgements",
        "consensus",
        "effective_verdict",
        "score",
        "breakdown",
        "tier",
    }
    assert item["citation"]["ref"] == "https://x.com"
    assert item["consensus"] == "Match"
    assert item["effective_verdict"] == "Match"
    assert item["tier"] in {"A", "B", "C", "F"}


def test_render_json_exposes_effective_uncertain_verdict():
    c = Citation(id=1, type="URL", ref="https://x.com", context="Smith 2017.", line=1)
    f = Fetched(id=1, status=200, title="Smith 2017", abstract="Smith reported it.")
    sc = score_citation(
        c,
        f,
        [
            Judgement("a", Verdict.MATCH, "supported"),
            Judgement("b", Verdict.MISMATCH, "not found"),
        ],
    )
    item = render_json(Report(source_file="doc.md", level="L2", scored=[sc]))["citations"][0]
    assert item["consensus"] == "Uncertain"
    assert item["effective_verdict"] == "Uncertain"
    assert item["breakdown"]["claim_match"] == 15


def test_render_json_is_serializable():
    # Must round-trip through json with no custom encoder.
    s = json.dumps(render_json(_report()))
    assert json.loads(s)["schema_version"] == SCHEMA_VERSION


def test_run_pipeline_l1_uses_url_alive_score_without_network_judges(monkeypatch):
    def fake_fetch_all(citations, level="L2", workers=4):
        return {
            c.id: Fetched(id=c.id, status=200 if c.ref.endswith("ok") else 404)
            for c in citations
        }

    monkeypatch.setattr(cli, "fetch_all", fake_fetch_all)

    report = cli.run_pipeline(
        "Good https://example.com/ok\nBad https://example.com/bad",
        level="L1",
        judge_specs=["keyword"],
    )
    out = render_json(report)
    assert out["level"] == "L1"
    assert out["judges"] == []
    assert out["overall_score"] == 50.0
    assert [c["score"] for c in out["citations"]] == [100.0, 0.0]
    assert [c["breakdown"] for c in out["citations"]] == [
        {"url_alive": 100},
        {"url_alive": 0},
    ]


def test_run_pipeline_records_judge_failures_without_crashing(monkeypatch):
    class BrokenJudge:
        name = "broken"

        def evaluate(self, claim_context, source_text):
            raise RuntimeError("missing key")

    monkeypatch.setattr(
        cli,
        "fetch_all",
        lambda citations, level="L2", workers=4: {
            c.id: Fetched(id=c.id, status=200, title="Smith 2017", abstract="Smith reported it in 2017.")
            for c in citations
        },
    )
    monkeypatch.setattr(cli, "make_judge", lambda spec: BrokenJudge())

    report = cli.run_pipeline("Smith 2017 says it. https://example.com/ref", judge_specs=["broken"])
    item = render_json(report)["citations"][0]

    assert item["judgements"][0]["judge"] == "broken"
    assert item["judgements"][0]["verdict"] == "Inaccessible"
    assert "missing key" in item["judgements"][0]["reason"]


def test_cli_json_emits_valid_json_to_stdout(tmp_path):
    src = tmp_path / "empty.md"
    src.write_text("No citations here.", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable, "-m", "paperverify.cli",
            str(src), "--level", "L1", "--judge", "keyword",
            "--json", "--out", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # stdout must be pure, parseable JSON (human summary went to stderr).
    data = json.loads(proc.stdout)
    assert set(data.keys()) == _TOP_LEVEL_KEYS
    assert data["level"] == "L1"
    assert data["source_file"].endswith("empty.md")
    assert data["citations"] == []
    # --out is additive: files were still written.
    assert (tmp_path / "empty_report.md").is_file()
    assert (tmp_path / "empty_claims.jsonl").is_file()


def test_cli_json_human_summary_on_stderr_not_stdout(tmp_path):
    src = tmp_path / "empty.md"
    src.write_text("No citations here.", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable, "-m", "paperverify.cli",
            str(src), "--level", "L1", "--judge", "keyword", "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    json.loads(proc.stdout)  # stdout parses cleanly
    assert "Overall:" in proc.stderr  # human summary is on stderr
