"""H1: an L1 sweep must not score a *dead landing page* as URL-alive.

The metadata path can return ``Fetched(status=200, landing_status=404)`` — the
metadata API answered (200) but the human-visible landing URL is gone (404).
``ok`` is True (the fetch status is 2xx), so the old L1 rubric awarded the full
100 "url_alive". That reports a dead link as live. The landing status, when it
was actually probed (``landing_status is not None``) and is non-2xx, must drop
``url_alive`` to 0 — "metadata exists" and "the URL resolves" are different
claims (audit H1).
"""

from paperverify.models import Citation, Fetched
from paperverify.score import score_citation


def _cite():
    return Citation(id=1, type="URL", ref="https://doi.org/10.1/x", context="ctx", line=1)


def test_l1_dead_landing_scores_zero_not_full():
    # status=200 (metadata API answered) but the landing URL is 404 (dead link).
    f = Fetched(id=1, status=200, landing_status=404, soft_404_suspect=False)
    sc = score_citation(_cite(), f, [], level="L1")
    assert sc.breakdown["url_alive"] == 0, "dead landing must not score url_alive=100"
    assert sc.score == 0.0


def test_l1_live_landing_still_full():
    # landing probed and 2xx -> still a live link, full credit.
    f = Fetched(id=1, status=200, landing_status=200)
    sc = score_citation(_cite(), f, [], level="L1")
    assert sc.breakdown["url_alive"] == 100


def test_l1_landing_not_probed_unchanged():
    # landing_status None (not a metadata hit / not probed) -> behaviour unchanged.
    f = Fetched(id=1, status=200, landing_status=None)
    sc = score_citation(_cite(), f, [], level="L1")
    assert sc.breakdown["url_alive"] == 100


def test_l1_dead_landing_overrides_soft_404_path():
    # Even if soft_404 would have given 50, a hard non-2xx landing is 0.
    f = Fetched(id=1, status=200, landing_status=410, soft_404_suspect=True)
    sc = score_citation(_cite(), f, [], level="L1")
    assert sc.breakdown["url_alive"] == 0
