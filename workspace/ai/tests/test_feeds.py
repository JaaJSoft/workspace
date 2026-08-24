from django.test import TestCase

from workspace.ai.services.feeds import looks_like_feed, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>The Blog</title>
  <description>Everything we publish</description>
  <link>https://example.com</link>
  <item>
    <title>Hello &amp; welcome</title>
    <link>/posts/1</link>
    <pubDate>Tue, 03 Jun 2025 09:00:00 GMT</pubDate>
    <description>&lt;p&gt;A &lt;b&gt;rich&lt;/b&gt; summary&lt;/p&gt;</description>
  </item>
  <item>
    <title>Second post</title>
    <guid>https://example.com/posts/2</guid>
    <pubDate>Wed, 04 Jun 2025 09:00:00 +0200</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Site</title>
  <subtitle>Notes and releases</subtitle>
  <entry>
    <title>Release 2.0</title>
    <link rel="edit" href="/edit/1"/>
    <link rel="alternate" href="/releases/2"/>
    <updated>2026-01-02T10:00:00Z</updated>
    <summary>What changed</summary>
  </entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel><title>RDF Feed</title></channel>
  <item rdf:about="https://example.com/r/1">
    <title>RDF item</title>
    <dc:date>2024-11-05T00:00:00Z</dc:date>
  </item>
</rdf:RDF>"""


class LooksLikeFeedTests(TestCase):
    def test_recognizes_each_dialect(self):
        for document in (RSS, ATOM, RDF):
            self.assertTrue(looks_like_feed(document[:400]))

    def test_html_is_not_a_feed(self):
        self.assertFalse(looks_like_feed(b"<!doctype html><html><head><title>x"))


class ParseFeedTests(TestCase):
    def test_rss_entries(self):
        feed = parse_feed(RSS, "https://example.com/feed.xml")

        self.assertEqual(feed.title, "The Blog")
        self.assertEqual(feed.subtitle, "Everything we publish")
        first, second = feed.entries
        self.assertEqual(first.title, "Hello & welcome")
        self.assertEqual(first.url, "https://example.com/posts/1")
        self.assertEqual(first.date, "2025-06-03")
        # The summary arrives as escaped HTML and is read as one plain line.
        self.assertEqual(first.summary, "A rich summary")
        self.assertEqual(second.url, "https://example.com/posts/2")

    def test_atom_prefers_the_alternate_link(self):
        feed = parse_feed(ATOM, "https://example.com/atom.xml")

        entry = feed.entries[0]
        self.assertEqual(feed.title, "Atom Site")
        self.assertEqual(entry.url, "https://example.com/releases/2")
        self.assertEqual(entry.date, "2026-01-02")

    def test_rdf_item_uses_its_about_attribute(self):
        feed = parse_feed(RDF, "https://example.com/rdf.xml")

        entry = feed.entries[0]
        self.assertEqual(feed.title, "RDF Feed")
        self.assertEqual(entry.url, "https://example.com/r/1")
        self.assertEqual(entry.date, "2024-11-05")

    def test_unparsable_date_is_kept_as_written(self):
        feed = parse_feed(
            RSS.replace(b"Tue, 03 Jun 2025 09:00:00 GMT", b"last thursday"),
            "https://example.com/feed.xml",
        )

        self.assertEqual(feed.entries[0].date, "last thursday")

    def test_entry_cap(self):
        items = b"".join(
            b"<item><title>Post %d</title><link>/p/%d</link></item>" % (i, i)
            for i in range(10)
        )
        document = RSS.replace(b"</channel>", items + b"</channel>")

        feed = parse_feed(document, "https://example.com/feed.xml", max_entries=5)

        self.assertEqual(len(feed.entries), 5)

    def test_html_document_is_not_a_feed(self):
        self.assertIsNone(
            parse_feed(b"<html><body><p>hi</p></body></html>", "https://example.com/")
        )

    def test_malformed_xml_returns_none(self):
        self.assertIsNone(parse_feed(b"\x00\x01 not xml", "https://example.com/"))

    def test_entities_are_never_expanded(self):
        # An entity that redefines itself is how a 200-byte feed becomes a
        # gigabyte of memory; the parser must leave the reference alone.
        bomb = RSS.replace(
            b'<?xml version="1.0"?>',
            b'<?xml version="1.0"?><!DOCTYPE rss ['
            b'<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>',
        ).replace(b"<title>Hello &amp; welcome</title>", b"<title>&b;</title>")

        feed = parse_feed(bomb, "https://example.com/feed.xml")

        self.assertNotIn("aaaa", feed.entries[0].title)
