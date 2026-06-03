"""Tests for citation extraction — no network, no API keys."""

from paperverify.extract import extract


def test_extracts_all_types():
    text = (
        "See https://example.com/page and the DOI 10.1126/science.1225829, "
        "plus arXiv:1706.03762, PMC1234567, and PMID: 9876543 for details."
    )
    cites = extract(text)
    types = {c.type for c in cites}
    assert types == {"URL", "DOI", "PMC", "PMID", "arXiv"}
    assert len(cites) == 5
    # ids assigned 1..N in order
    assert [c.id for c in cites] == [1, 2, 3, 4, 5]


def test_url_trailing_punctuation_trimmed():
    cites = extract("Reference: https://example.com/x).")
    urls = [c for c in cites if c.type == "URL"]
    assert len(urls) == 1
    assert urls[0].ref == "https://example.com/x"


def test_dedupe_same_ref():
    text = "https://a.com here, and again https://a.com there."
    cites = extract(text)
    assert len([c for c in cites if c.type == "URL"]) == 1


def test_dedupe_is_case_insensitive_for_ref():
    text = "doi 10.1000/AbC and again 10.1000/abc"
    cites = [c for c in extract(text) if c.type == "DOI"]
    assert len(cites) == 1


def test_context_and_line_number():
    text = "line one\nline two with https://example.com/here\nline three"
    cites = extract(text)
    url = next(c for c in cites if c.type == "URL")
    assert url.line == 2
    assert "line two" in url.context
    assert "\n" not in url.context  # newlines collapsed


def test_empty_text_returns_empty():
    assert extract("") == []


def test_no_citations():
    assert extract("just some prose with no references at all.") == []


def test_pmid_and_pmc_distinct():
    cites = extract("PMC42 and PMID: 99")
    refs = {(c.type, c.ref) for c in cites}
    assert ("PMC", "PMC42") in refs
    assert any(t == "PMID" for t, _ in refs)
