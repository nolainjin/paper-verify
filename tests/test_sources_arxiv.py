"""arXiv adapter must reject the API's error feed (audit SA-01 / P0-1).

arXiv returns HTTP 200 with an <entry><title>Error</title></entry> feed for
unknown/malformed ids; the adapter must not treat that as a verified paper.
"""

import paperverify.sources as sources

_ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_9999.99999</id>
    <title>Error</title>
    <summary>incorrect id format for 9999.99999</summary>
    <author><name>arXiv api core</name></author>
  </entry>
</feed>"""

_VALID_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v5</id>
    <title>Attention Is All You Need</title>
    <summary>The dominant sequence transduction models...</summary>
    <author><name>Ashish Vaswani</name></author>
    <published>2017-06-12T00:00:00Z</published>
  </entry>
</feed>"""


def test_arxiv_error_feed_returns_none(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _ERROR_FEED)
    assert sources.fetch_arxiv_metadata("9999.99999") is None


def test_arxiv_valid_feed_still_parses(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _VALID_FEED)
    meta = sources.fetch_arxiv_metadata("1706.03762")
    assert meta is not None
    assert meta["title"] == "Attention Is All You Need"
    assert meta["year"] == 2017
