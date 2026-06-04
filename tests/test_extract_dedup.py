"""A DOI/arXiv/PMID embedded inside a matched URL must not be counted as a
second, separate citation (audit CL-1 / P0-6). fetch._metadata_for already
routes URL-type citations carrying a DOI to Crossref, so the URL alone is
enough — the duplicate inflated the score average and double-fetched.
"""

from paperverify.extract import extract


def test_doi_inside_url_not_double_counted():
    cites = extract("See https://doi.org/10.1126/science.1225829 for details.")
    assert len(cites) == 1
    assert cites[0].type == "URL"


def test_arxiv_inside_url_not_double_counted():
    cites = extract("Paper at https://arxiv.org/abs/1706.03762 is seminal.")
    assert len(cites) == 1
    assert cites[0].type == "URL"


def test_standalone_doi_still_extracted():
    cites = extract("As shown in 10.1126/science.1225829 (no URL).")
    assert len(cites) == 1
    assert cites[0].type == "DOI"


def test_url_plus_separate_standalone_doi_both_kept():
    text = "Site https://example.com/page and separately 10.1000/xyz123 cited."
    cites = extract(text)
    types = sorted(c.type for c in cites)
    assert types == ["DOI", "URL"]
