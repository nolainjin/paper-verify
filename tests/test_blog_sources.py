from paperverify import cli
from paperverify.judge import KeywordJudge
from paperverify.models import Fetched, Judgement, Verdict
from paperverify.report import render_json


class SplitJudge:
    name = "split"

    def evaluate(self, claim_context, source_text):
        return Judgement(self.name, Verdict.MISMATCH, "blog claim is not supported")


def test_blog_source_verification_surfaces_review_shortlist_signals(monkeypatch):
    blog = (
        "## Market note\n\n"
        "The city launched a new transit card in 2024 after the pilot program "
        "succeeded (https://news.example.com/transit-card).\n\n"
        "The app's retention doubled in one week according to a vendor blog "
        "(https://vendor.example.com/deleted-case-study).\n\n"
        "A public dashboard still explains the baseline data "
        "(https://data.example.com/dashboard).\n"
    )

    def fake_fetch_all(citations, level="L2", workers=4):
        by_ref = {c.ref: c.id for c in citations}
        return {
            by_ref["https://news.example.com/transit-card"]: Fetched(
                id=by_ref["https://news.example.com/transit-card"],
                status=200,
                title="Transit card pilot 2024",
                abstract="The city launched a transit card in 2024 after a successful pilot program.",
                source="http",
            ),
            by_ref["https://vendor.example.com/deleted-case-study"]: Fetched(
                id=by_ref["https://vendor.example.com/deleted-case-study"],
                status=200,
                title="Not found",
                abstract="This page does not exist.",
                source="http",
                soft_404_suspect=True,
            ),
            by_ref["https://data.example.com/dashboard"]: Fetched(
                id=by_ref["https://data.example.com/dashboard"],
                status=404,
                error="404",
                source="none",
            ),
        }

    monkeypatch.setattr(cli, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        cli,
        "make_judge",
        lambda spec: KeywordJudge() if spec == "keyword" else SplitJudge(),
    )

    report = cli.run_pipeline(blog, level="L2", judge_specs=["keyword", "split"])
    data = render_json(report)
    risky = [
        item
        for item in data["citations"]
        if item["tier"] in {"C", "F"}
        or item["consensus"] in {"Uncertain", "Mismatch", "Inaccessible"}
        or item["fetched"]["soft_404_suspect"]
        or item["fetched"]["source"] == "none"
    ]

    assert len(data["citations"]) == 3
    assert len(risky) == 3
    assert any(item["fetched"]["soft_404_suspect"] for item in risky)
    assert any(item["fetched"]["source"] == "none" for item in risky)
    assert all(item["consensus"] in {"Uncertain", "Mismatch", "Inaccessible"} for item in risky)


def test_blog_l1_dead_link_sweep_is_network_free_and_structured(monkeypatch):
    blog = "Good https://blog.example.com/live and bad https://blog.example.com/missing"

    def fake_fetch_all(citations, level="L1", workers=4):
        return {
            c.id: Fetched(id=c.id, status=200 if c.ref.endswith("/live") else 404)
            for c in citations
        }

    monkeypatch.setattr(cli, "fetch_all", fake_fetch_all)

    data = render_json(cli.run_pipeline(blog, level="L1"))
    assert data["level"] == "L1"
    assert data["judges"] == []
    assert [item["breakdown"]["url_alive"] for item in data["citations"]] == [100, 0]
    assert [item["consensus"] for item in data["citations"]] == [None, None]
