"""Offline evidence scoring (--from-evidence) — no network, no API keys."""

import json
from pathlib import Path

import pytest

from paperverify import cli
from paperverify.offline import EvidenceError, report_from_evidence
from paperverify.report import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


def _evidence(**over):
    data = {
        "source_file": "demo.md",
        "level": "L2",
        "citations": [
            {
                "citation": {
                    "type": "URL",
                    "ref": "https://example.org/a",
                    "context": "Smith (2020) shows X",
                    "line": 3,
                },
                "fetched": {
                    "status": 200,
                    "title": "X study",
                    "abstract": "Smith 2020 shows X.",
                    "url_final": "https://example.org/a",
                    "source": "http",
                },
                "judgements": [
                    {"judge": "webchat:claude", "verdict": "Match", "reason": "explicitly supported"}
                ],
            },
            {
                "citation": {
                    "type": "DOI",
                    "ref": "10.1000/xyz",
                    "context": "claims Y rose 40%",
                    "line": 9,
                },
                "fetched": {
                    "status": 200,
                    "title": "Y paper",
                    "abstract": "Y fell.",
                    "source": "crossref",
                    "year": 2021,
                },
                "judgements": [
                    {"judge": "webchat:claude", "verdict": "Mismatch", "reason": "contradicted"}
                ],
            },
        ],
    }
    data.update(over)
    return data


def test_happy_path_scores_with_standard_rubric():
    report = report_from_evidence(_evidence())
    assert report.level == "L2"
    assert report.source_file == "demo.md"
    assert len(report.scored) == 2
    match, mismatch = report.scored
    assert match.breakdown["claim_match"] == 50
    assert mismatch.breakdown["claim_match"] == 0
    assert match.breakdown["url_accessible"] == 20
    # single judge -> no cross-check credit, identical to the CLI pipeline
    assert match.breakdown["cross_check"] == 0
    assert report.judges == ["webchat:claude"]


def test_ids_are_assigned_when_missing():
    report = report_from_evidence(_evidence())
    assert [sc.citation.id for sc in report.scored] == [1, 2]


def test_unknown_verdict_names_citation_index():
    bad = _evidence()
    bad["citations"][1]["judgements"][0]["verdict"] = "Confirmed"
    with pytest.raises(EvidenceError, match=r"citations\[1\].judgements\[0\]"):
        report_from_evidence(bad)


def test_missing_ref_names_citation_index():
    bad = _evidence()
    del bad["citations"][0]["citation"]["ref"]
    with pytest.raises(EvidenceError, match=r"citations\[0\]"):
        report_from_evidence(bad)


def test_citations_must_be_a_list():
    with pytest.raises(EvidenceError, match="'citations' must be a list"):
        report_from_evidence({"citations": {}})


def test_level_defaults_to_l2_and_validates():
    report = report_from_evidence({"citations": []})
    assert report.level == "L2"
    with pytest.raises(EvidenceError, match="unknown level"):
        report_from_evidence({"level": "L9", "citations": []})


def test_fetched_may_be_null():
    ev = _evidence()
    ev["citations"][0]["fetched"] = None
    report = report_from_evidence(ev)
    assert report.scored[0].breakdown["url_accessible"] == 0


def test_bad_year_type_is_a_clear_error():
    ev = _evidence()
    ev["citations"][1]["fetched"]["year"] = "n/a"
    with pytest.raises(EvidenceError, match=r"citations\[1\].fetched"):
        report_from_evidence(ev)
