import json
from typing import get_args
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from workspace.ai.services.reading import Extraction, Finding
from workspace.ai.services.web import (
    MAX_LINKS,
    MAX_RESPONSE_BYTES,
    SEARCH_CATEGORIES,
    SEARCH_TIME_RANGES,
    _absolutize_links,
    _is_url_safe,
    _site_domain,
    fetch_and_extract,
    search,
    search_many,
)
from workspace.ai.tools import (
    WEB_SEARCH_MAX_RESULTS,
    ReadWebpageParams,
    WebSearchParams,
    WebToolProvider,
)
from workspace.common.tests.pdf_fixtures import make_pdf


class IsUrlSafeTests(TestCase):
    def test_localhost_blocked(self):
        self.assertFalse(_is_url_safe("http://localhost/admin"))

    def test_127_blocked(self):
        self.assertFalse(_is_url_safe("http://127.0.0.1:8000/secret"))

    def test_private_10_blocked(self):
        self.assertFalse(_is_url_safe("http://10.0.0.1/internal"))

    def test_private_192_blocked(self):
        self.assertFalse(_is_url_safe("http://192.168.1.1/router"))

    def test_private_172_blocked(self):
        self.assertFalse(_is_url_safe("http://172.16.0.1/internal"))

    def test_public_url_allowed(self):
        self.assertTrue(_is_url_safe("https://example.com/page"))

    def test_public_ip_allowed(self):
        self.assertTrue(_is_url_safe("http://8.8.8.8/"))

    def test_ipv6_loopback_blocked(self):
        self.assertFalse(_is_url_safe("http://[::1]/"))

    @override_settings(SEARXNG_BLOCKED_DOMAINS="evil.com,spam.org")
    def test_blocked_domain_exact(self):
        self.assertFalse(_is_url_safe("https://evil.com/page"))

    @override_settings(SEARXNG_BLOCKED_DOMAINS="evil.com")
    def test_blocked_domain_subdomain(self):
        self.assertFalse(_is_url_safe("https://sub.evil.com/page"))

    @override_settings(SEARXNG_BLOCKED_DOMAINS="evil.com")
    def test_blocked_domain_allows_other(self):
        self.assertTrue(_is_url_safe("https://example.com/page"))

    @override_settings(SEARXNG_BLOCKED_DOMAINS="Evil.COM")
    def test_blocked_domain_case_insensitive(self):
        self.assertFalse(_is_url_safe("https://EVIL.com/page"))

    @override_settings(SEARXNG_BLOCKED_DOMAINS="")
    def test_empty_blocklist_allows_all(self):
        self.assertTrue(_is_url_safe("https://anything.com/"))


@override_settings(SEARXNG_URL="http://searxng:8080")
class SearchTests(TestCase):
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_search_returns_results(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"title": "Result 1", "url": "https://a.com", "content": "Snippet 1"},
                {"title": "Result 2", "url": "https://b.com", "content": "Snippet 2"},
            ],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = search("test query", max_results=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Result 1")
        self.assertEqual(results[1]["url"], "https://b.com")

    @override_settings(SEARXNG_URL="")
    def test_search_disabled_when_no_url(self):
        results = search("test")
        self.assertEqual(results, [])

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_search_handles_error(self, mock_client_cls):
        import httpx2

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx2.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        results = search("failing query")

        self.assertEqual(results, [])

    def _capture_params(self, mock_client_cls, results=()):
        """Wire a client stub returning *results* and hand back its GET params."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": list(results)}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        return mock_client

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_defaults_send_no_time_filter(self, mock_client_cls):
        client = self._capture_params(mock_client_cls)

        search("test")

        params = client.get.call_args.kwargs["params"]
        self.assertNotIn("time_range", params)
        self.assertEqual(params["categories"], "general")
        self.assertEqual(params["q"], "test")

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_time_range_and_category_are_forwarded(self, mock_client_cls):
        client = self._capture_params(mock_client_cls)

        search("test", time_range="week", category="news")

        params = client.get.call_args.kwargs["params"]
        self.assertEqual(params["time_range"], "week")
        self.assertEqual(params["categories"], "news")

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_unknown_filters_are_dropped_rather_than_sent(self, mock_client_cls):
        # SearXNG answers an unknown time_range with a 4xx, which would turn a
        # slightly wrong filter into no results at all.
        client = self._capture_params(mock_client_cls)

        search("test", time_range="fortnight", category="recipes")

        params = client.get.call_args.kwargs["params"]
        self.assertNotIn("time_range", params)
        self.assertEqual(params["categories"], "general")

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_site_becomes_a_site_operator(self, mock_client_cls):
        client = self._capture_params(mock_client_cls)

        search("release notes", site="https://docs.python.org/3/")

        self.assertEqual(
            client.get.call_args.kwargs["params"]["q"],
            "site:docs.python.org release notes",
        )

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_max_results_caps_the_returned_list(self, mock_client_cls):
        self._capture_params(
            mock_client_cls,
            [{"title": f"r{i}", "url": f"https://a.com/{i}"} for i in range(9)],
        )

        self.assertEqual(len(search("test", max_results=3)), 3)


class SiteDomainTests(TestCase):
    def test_bare_domain_kept(self):
        self.assertEqual(_site_domain("example.com"), "example.com")

    def test_scheme_and_path_stripped(self):
        self.assertEqual(_site_domain("https://example.com/a/b?c=1"), "example.com")

    def test_operator_prefix_stripped(self):
        self.assertEqual(_site_domain("site: Example.COM"), "example.com")

    def test_empty_stays_empty(self):
        self.assertEqual(_site_domain("   "), "")


@override_settings(SEARXNG_URL="http://searxng:8080")
class SearchManyTests(TestCase):
    def test_single_query_returns_the_plain_search_shape(self):
        with patch(
            "workspace.ai.services.web.search",
            return_value=[{"title": "t", "url": "https://a.com", "snippet": "s"}],
        ) as mock_search:
            results = search_many(["only"], max_results=3)

        mock_search.assert_called_once_with("only", max_results=3)
        self.assertNotIn("query", results[0])

    def test_no_query_searches_nothing(self):
        # ThreadPoolExecutor rejects a pool of zero workers.
        self.assertEqual(search_many([]), [])

    def test_results_are_tagged_with_the_query_that_found_them(self):
        by_query = {
            "alpha": [{"title": "A", "url": "https://a.com", "snippet": ""}],
            "beta": [{"title": "B", "url": "https://b.com", "snippet": ""}],
        }
        with patch(
            "workspace.ai.services.web.search", side_effect=lambda q, **kw: by_query[q]
        ):
            results = search_many(["alpha", "beta"])

        self.assertEqual(
            [(r["url"], r["query"]) for r in results],
            [("https://a.com", "alpha"), ("https://b.com", "beta")],
        )

    def test_url_found_twice_is_listed_once(self):
        shared = [{"title": "Shared", "url": "https://a.com", "snippet": ""}]
        with patch("workspace.ai.services.web.search", return_value=list(shared)):
            results = search_many(["alpha", "beta"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["query"], "alpha")

    def test_every_query_gets_the_same_filters(self):
        with patch("workspace.ai.services.web.search", return_value=[]) as mock_search:
            search_many(["alpha", "beta"], time_range="day", site="a.com")

        for call in mock_search.call_args_list:
            self.assertEqual(call.kwargs["time_range"], "day")
            self.assertEqual(call.kwargs["site"], "a.com")


def _fake_response(*, text="", url="https://example.com/", content_type="text/html"):
    """Build a response stub whose headers and url are real strings.

    A bare MagicMock answers every attribute, so the content-type branch and
    the link base would both be driven by mock objects rather than the values
    under test.
    """
    resp = MagicMock()
    resp.text = text
    resp.content = text.encode()
    resp.url = url
    resp.headers = {"content-type": content_type}
    return resp


def _client_returning(resp):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = resp
    return client


class AbsolutizeLinksTests(TestCase):
    def test_relative_link_resolved_against_base(self):
        md = "see [the files](/owner/repo/pull/2/files) for details"
        out = _absolutize_links(md, "https://example.com/owner/repo/pull/2")
        self.assertIn("(https://example.com/owner/repo/pull/2/files)", out)

    def test_absolute_link_untouched(self):
        md = "[docs](https://other.com/a)"
        self.assertEqual(_absolutize_links(md, "https://example.com/"), md)

    def test_text_without_links_unchanged(self):
        md = "no links here (just parentheses)"
        self.assertEqual(_absolutize_links(md, "https://example.com/"), md)


class FetchAndExtractTests(TestCase):
    def test_private_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fetch_and_extract("http://localhost:8000/admin/")
        self.assertIn("private", str(ctx.exception))

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_extracts_content(self, mock_client_cls, mock_extract):
        resp = _fake_response(text="<html><body><p>Hello world</p></body></html>")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "Hello world"

        text = fetch_and_extract("https://example.com/article")

        self.assertIn("Hello world", text)
        mock_extract.assert_called_once()

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_extraction_keeps_links_as_markdown(self, mock_client_cls, mock_extract):
        mock_client_cls.return_value = _client_returning(_fake_response(text="<html/>"))
        mock_extract.return_value = "text"

        fetch_and_extract("https://example.com/article")

        kwargs = mock_extract.call_args.kwargs
        self.assertTrue(kwargs["include_links"])
        self.assertEqual(kwargs["output_format"], "markdown")

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_relative_links_are_absolutized(self, mock_client_cls, mock_extract):
        resp = _fake_response(text="<html/>", url="https://example.com/a/b")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "read [more](../c) now"

        text = fetch_and_extract("https://example.com/a/b")

        self.assertIn("(https://example.com/c)", text)

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_json_response_returned_as_json(self, mock_client_cls, mock_extract):
        resp = _fake_response(
            text='{"title": "PR title", "state": "open"}',
            content_type="application/json; charset=utf-8",
        )
        resp.json.return_value = {"title": "PR title", "state": "open"}
        mock_client_cls.return_value = _client_returning(resp)

        text = fetch_and_extract("https://example.com/api/pulls/1")

        self.assertEqual(text, '{"title":"PR title","state":"open"}')
        mock_extract.assert_not_called()

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_malformed_json_falls_back_to_extraction(
        self, mock_client_cls, mock_extract
    ):
        resp = _fake_response(text="not json", content_type="application/json")
        resp.json.side_effect = ValueError("no")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "not json"

        self.assertIn("not json", fetch_and_extract("https://example.com/x"))

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_truncates_long_content(self, mock_client_cls, mock_extract):
        mock_client_cls.return_value = _client_returning(_fake_response(text="<html/>"))
        mock_extract.return_value = "A" * 10000

        text = fetch_and_extract("https://example.com/", max_chars=400)

        # The marker counts against the budget, so the caller never gets more
        # than the number of characters it asked for.
        self.assertLessEqual(len(text), 400)
        self.assertIn("AAAA", text)
        self.assertIn("read this URL again with part=2", text)

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_json_truncation_also_respects_the_budget(
        self, mock_client_cls, mock_extract
    ):
        resp = _fake_response(text="{}", content_type="application/json")
        resp.json.return_value = {"body": "B" * 5000}
        mock_client_cls.return_value = _client_returning(resp)

        text = fetch_and_extract("https://example.com/api", max_chars=200)

        self.assertLessEqual(len(text), 200)
        self.assertIn("read this URL again with part=2", text)

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_links_resolve_against_the_redirected_url(
        self, mock_client_cls, mock_extract
    ):
        # httpx exposes the URL the redirects landed on; a link relative to it
        # resolves to a different page than the same link read against the
        # URL originally requested.
        resp = _fake_response(text="<html/>", url="https://cdn.example.com/docs/page")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "see [next](other)"

        text = fetch_and_extract("https://example.com/start")

        self.assertIn("(https://cdn.example.com/docs/other)", text)
        self.assertEqual(
            mock_extract.call_args.kwargs["url"], "https://cdn.example.com/docs/page"
        )

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_fallback_when_trafilatura_returns_empty(
        self, mock_client_cls, mock_extract
    ):
        resp = _fake_response(text="<html><body><p>Fallback text</p></body></html>")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = None  # trafilatura failed

        text = fetch_and_extract("https://example.com/")

        self.assertIn("Fallback text", text)

    @patch("workspace.ai.services.web.httpx2.Client")
    def test_rejects_oversized_response(self, mock_client_cls):
        resp = _fake_response()
        resp.content = b"x" * (MAX_RESPONSE_BYTES + 1)
        mock_client_cls.return_value = _client_returning(resp)

        with self.assertRaises(ValueError) as ctx:
            fetch_and_extract("https://example.com/huge")
        self.assertIn("too large", str(ctx.exception))


class WebSearchToolTests(TestCase):
    """The tool layer: argument normalisation and the empty-result message."""

    def _run(self, **kwargs):
        provider = WebToolProvider()
        args = WebSearchParams(**kwargs)
        return provider.web_search(args, None, None, None, {})

    def test_queries_are_deduplicated_stripped_and_capped(self):
        with patch(
            "workspace.ai.services.web.search_many", return_value=[]
        ) as mock_search:
            self._run(queries=["  a  ", "a", "", "b", "c", "d", "e"])

        self.assertEqual(mock_search.call_args.args[0], ["a", "b", "c", "d"])

    def test_blank_queries_are_refused(self):
        result = self._run(queries=["   ", ""])
        self.assertTrue(result.startswith("Error:"))

    def test_filters_reach_the_service(self):
        with patch(
            "workspace.ai.services.web.search_many", return_value=[]
        ) as mock_search:
            self._run(
                queries=["q"],
                time_range="week",
                category="news",
                site="example.com",
                max_results=12,
            )

        kwargs = mock_search.call_args.kwargs
        self.assertEqual(kwargs["time_range"], "week")
        self.assertEqual(kwargs["category"], "news")
        self.assertEqual(kwargs["site"], "example.com")
        self.assertEqual(kwargs["max_results"], 12)

    def test_max_results_is_clamped_to_the_ceiling(self):
        with patch(
            "workspace.ai.services.web.search_many", return_value=[]
        ) as mock_search:
            self._run(queries=["q"], max_results=500)

        self.assertEqual(
            mock_search.call_args.kwargs["max_results"], WEB_SEARCH_MAX_RESULTS
        )

    def test_empty_unfiltered_search_reports_nothing_found(self):
        with patch("workspace.ai.services.web.search_many", return_value=[]):
            self.assertEqual(self._run(queries=["q"]), "No results found.")

    def test_empty_filtered_search_names_the_restrictions(self):
        # A narrowed search that found nothing reads like an empty web unless
        # the filters are named, and the model then stops instead of widening.
        with patch("workspace.ai.services.web.search_many", return_value=[]):
            result = self._run(
                queries=["q"], time_range="day", category="news", site="example.com"
            )

        self.assertIn("the last day", result)
        self.assertIn("news category", result)
        self.assertIn("example.com", result)
        self.assertIn("Retry", result)

    def test_results_are_returned_as_json(self):
        payload = [{"title": "T", "url": "https://a.com", "snippet": "s"}]
        with patch("workspace.ai.services.web.search_many", return_value=payload):
            result = self._run(queries=["q"])

        self.assertEqual(json.loads(result), payload)


class WebSearchParamsTests(TestCase):
    def test_schema_offers_exactly_the_filters_the_service_honours(self):
        # The service drops a filter it doesn't know, so a value advertised
        # here but absent there would be accepted and then silently ignored.
        time_ranges = get_args(WebSearchParams.model_fields["time_range"].annotation)
        categories = get_args(WebSearchParams.model_fields["category"].annotation)
        self.assertEqual(set(time_ranges) - {""}, set(SEARCH_TIME_RANGES))
        self.assertEqual(set(categories), set(SEARCH_CATEGORIES))

    def test_a_bare_string_is_read_as_one_query(self):
        self.assertEqual(WebSearchParams(queries="cats").queries, ["cats"])

    def test_defaults_leave_the_search_unfiltered(self):
        params = WebSearchParams(queries=["cats"])
        self.assertEqual(params.time_range, "")
        self.assertEqual(params.category, "general")
        self.assertEqual(params.site, "")


_ARTICLE_HTML = """<html><head>
<title>The headline</title>
<meta name="author" content="Jane Doe">
<meta property="article:published_time" content="2024-05-01T10:00:00Z">
</head><body><article><p>Body text.</p></article></body></html>"""


class PageHeaderTests(TestCase):
    """The header that opens a result: title, date, author, final URL."""

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_metadata_precedes_the_text(self, mock_client_cls, mock_extract):
        resp = _fake_response(text=_ARTICLE_HTML, url="https://example.com/article")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "Body text."

        text = fetch_and_extract("https://example.com/article")

        header, _, body = text.partition("\n\n")
        self.assertIn("# The headline", header)
        self.assertIn("Source: https://example.com/article", header)
        self.assertIn("Published: 2024-05-01", header)
        self.assertIn("By: Jane Doe", header)
        self.assertEqual(body, "Body text.")

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_header_names_the_url_the_redirects_landed_on(
        self, mock_client_cls, mock_extract
    ):
        # The URL a citation has to name is the one that was read, and the bot
        # is otherwise never told the two differ.
        resp = _fake_response(text="<html/>", url="https://cdn.example.com/final")
        mock_client_cls.return_value = _client_returning(resp)
        mock_extract.return_value = "text"

        text = fetch_and_extract("https://example.com/start")

        self.assertIn("Source: https://cdn.example.com/final", text)

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_page_without_metadata_still_reports_its_url(
        self, mock_client_cls, mock_extract
    ):
        mock_client_cls.return_value = _client_returning(
            _fake_response(text="<html><body>x</body></html>")
        )
        mock_extract.return_value = "text"

        self.assertIn(
            "Source: https://example.com/", fetch_and_extract("https://example.com/")
        )


class PageLinkListTests(TestCase):
    def _fetch(self, markdown, *, url="https://example.com/page", max_chars=12000):
        with (
            patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls,
            patch("workspace.ai.services.web.trafilatura.extract") as mock_extract,
        ):
            mock_client_cls.return_value = _client_returning(
                _fake_response(text="<html/>", url=url)
            )
            mock_extract.return_value = markdown
            return fetch_and_extract(url, max_chars=max_chars)

    def test_links_are_grouped_by_destination(self):
        text = self._fetch(
            "See [the detail page](/detail) and [another source](https://other.org/x)."
        )

        links = text.partition("## Links")[2]
        self.assertIn("- [the detail page](https://example.com/detail)", links)
        self.assertIn("- [another source](https://other.org/x)", links)
        self.assertLess(
            links.index("[the detail page]"), links.index("[another source]")
        )

    def test_repeated_link_is_listed_once(self):
        text = self._fetch(
            "[the detail page](/detail) then [the detail page](/detail) again, "
            "and [the detail page](/detail#section) once more"
        )

        self.assertEqual(text.count("- [the detail page]"), 1)

    def test_navigation_chrome_is_dropped(self):
        # Extraction keeps pagination arrows; they are not a place to go next.
        text = self._fetch("[»](/next) [1](/p/1) [a real link](/article)")

        links = text.partition("## Links")[2]
        self.assertIn("[a real link]", links)
        self.assertNotIn("/next", links)
        self.assertNotIn("/p/1", links)

    def test_link_list_is_capped_and_says_what_it_dropped(self):
        markdown = " ".join(
            f"[internal link {i}](/i/{i}) [external link {i}](https://other.org/{i})"
            for i in range(40)
        )

        links = self._fetch(markdown).partition("## Links")[2]

        self.assertEqual(links.count("\n- "), MAX_LINKS)
        self.assertIn("more links not listed", links)
        # Both destinations survive the cap: one is not a substitute for the other.
        self.assertIn("https://example.com/i/", links)
        self.assertIn("https://other.org/", links)

    def test_page_without_links_has_no_link_section(self):
        self.assertNotIn("## Links", self._fetch("Plain prose, no links."))

    def test_links_never_push_the_result_over_the_budget(self):
        markdown = "A" * 5000 + " ".join(
            f"[a link with a long anchor {i}](https://other.org/{i})" for i in range(40)
        )

        text = self._fetch(markdown, max_chars=1000)

        self.assertLessEqual(len(text), 1000)
        # The page is what was asked for; the link list is the part that goes.
        self.assertIn("AAAA", text)


class PdfFetchTests(TestCase):
    def _fetch(self, data, *, content_type="application/pdf", max_chars=12000):
        resp = _fake_response(
            url="https://example.com/doc.pdf", content_type=content_type
        )
        resp.content = data
        with patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls:
            mock_client_cls.return_value = _client_returning(resp)
            return fetch_and_extract("https://example.com/doc.pdf", max_chars=max_chars)

    def test_pdf_text_is_extracted(self):
        text = self._fetch(make_pdf(["First page", "Second page"]))

        self.assertIn("First page", text)
        self.assertIn("Second page", text)
        self.assertIn("Pages: 2", text)
        self.assertIn("Source: https://example.com/doc.pdf", text)

    def test_pdf_served_with_the_wrong_content_type_is_still_read(self):
        # Plenty of servers hand out application/octet-stream; the file itself
        # says what it is.
        text = self._fetch(
            make_pdf(["Body text"]), content_type="application/octet-stream"
        )

        self.assertIn("Body text", text)

    def test_scanned_pdf_says_so_instead_of_returning_nothing(self):
        text = self._fetch(make_pdf([""]))

        self.assertIn("no text layer", text)

    def test_pdf_gets_a_larger_size_budget_than_a_web_page(self):
        text = self._fetch(make_pdf(["Body text"], padding_bytes=3 * 1024 * 1024))

        self.assertIn("Body text", text)

    def test_oversized_pdf_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._fetch(make_pdf(["x"], padding_bytes=11 * 1024 * 1024))
        self.assertIn("PDF too large", str(ctx.exception))

    def test_unreadable_pdf_reports_the_failure(self):
        with self.assertRaises(ValueError) as ctx:
            self._fetch(b"%PDF-1.4 truncated right here")
        self.assertIn("Could not read PDF", str(ctx.exception))


_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>The Blog</title>
  <item>
    <title>Newest post</title><link>/posts/2</link>
    <pubDate>Tue, 03 Jun 2025 09:00:00 GMT</pubDate>
    <description>What it is about</description>
  </item>
  <item><title>Older post</title><link>/posts/1</link></item>
</channel></rss>"""


class FeedFetchTests(TestCase):
    def _fetch(self, body, *, content_type):
        with patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls:
            mock_client_cls.return_value = _client_returning(
                _fake_response(
                    text=body,
                    url="https://example.com/feed.xml",
                    content_type=content_type,
                )
            )
            return fetch_and_extract("https://example.com/feed.xml")

    def test_feed_becomes_a_dated_list_of_links(self):
        text = self._fetch(_RSS, content_type="application/rss+xml")

        self.assertIn("# The Blog", text)
        self.assertIn("Entries: 2", text)
        self.assertIn("- 2025-06-03 — [Newest post](https://example.com/posts/2)", text)
        self.assertIn("What it is about", text)

    def test_feed_served_as_generic_xml_is_recognized(self):
        # Feeds are routinely served as text/xml, which says nothing.
        self.assertIn("Newest post", self._fetch(_RSS, content_type="text/xml"))

    def test_feed_served_as_html_is_recognized(self):
        self.assertIn("Newest post", self._fetch(_RSS, content_type="text/html"))

    @patch("workspace.ai.services.web.trafilatura.extract")
    def test_xml_that_is_not_a_feed_falls_back_to_extraction(self, mock_extract):
        mock_extract.return_value = "Extracted prose"

        text = self._fetch(
            "<?xml version='1.0'?><sitemap><url>x</url></sitemap>",
            content_type="application/xml",
        )

        self.assertIn("Extracted prose", text)


_ANSWER = (
    "The API answers a 429 status once the cap is exceeded, and names the "
    "delay to wait in a Retry-After header."
)


def _manual_markdown() -> str:
    """A reference page whose answer sits well past the first characters."""
    return "\n\n".join(
        [
            "# Widget API reference",
            "Everything the service exposes is documented on this single page.",
            "## Authentication",
            *(
                f"Token note {i}: the credential is sent on every call, and the "
                "paragraph explaining it runs on."
                for i in range(20)
            ),
            "## Rate limiting",
            _ANSWER,
            "## Webhooks",
            *(
                f"Callback note {i}: the delivery contract spelled out at the "
                "length documentation is written at."
                for i in range(20)
            ),
        ]
    )


def _extracting(*findings, missing=""):
    """Stand in for the extraction model, reporting *findings* for every chunk."""
    return lambda *a, **kw: (
        Extraction(findings=[Finding(text=f) for f in findings], missing=missing),
        {},
    )


@override_settings(AI_SMALL_MODEL="small-model", AI_MODEL="big-model")
class QueryScopedFetchTests(TestCase):
    """The query threaded from the tool down to each renderer."""

    def _fetch_html(
        self, markdown, *, query="", says=(), missing="", max_chars=1600, part=1
    ):
        url = "https://example.com/manual"
        with (
            patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls,
            patch("workspace.ai.services.web.trafilatura.extract") as mock_extract,
            patch(
                "workspace.ai.services.reading.call_llm_structured",
                side_effect=_extracting(*says, missing=missing),
            ),
        ):
            mock_client_cls.return_value = _client_returning(
                _fake_response(text="<html/>", url=url)
            )
            mock_extract.return_value = markdown
            return fetch_and_extract(url, max_chars=max_chars, query=query, part=part)

    def test_query_returns_the_section_that_answers_it(self):
        text = self._fetch_html(
            _manual_markdown(), query="what happens at the cap?", says=(_ANSWER,)
        )

        self.assertIn(_ANSWER, text)
        self.assertNotIn("Token note 0", text)

    def test_scoped_read_still_names_its_source_and_its_links(self):
        markdown = _manual_markdown() + (
            "\n\n## See also\n\n[the rate limit policy](/policy) and "
            "[the status page](https://status.example.com/)"
        )

        text = self._fetch_html(
            markdown, query="what happens at the cap?", says=(_ANSWER,)
        )

        self.assertIn("Source: https://example.com/manual", text)
        self.assertIn("- [the rate limit policy](https://example.com/policy)", text)
        self.assertIn("- [the status page](https://status.example.com/)", text)

    def test_the_outline_says_what_the_query_left_out(self):
        text = self._fetch_html(
            _manual_markdown(), query="what happens at the cap?", says=(_ANSWER,)
        )

        self.assertIn("## Page outline", text)
        self.assertIn("- Webhooks", text)

    def test_without_a_query_the_page_is_read_from_the_top(self):
        text = self._fetch_html(_manual_markdown())

        self.assertIn("Everything the service exposes", text)
        self.assertIn("Token note 0", text)
        self.assertNotIn("## Page outline", text)
        self.assertIn("read this URL again with part=2", text)

    def test_the_part_asked_for_reaches_the_reading_step(self):
        with patch(
            "workspace.ai.services.web.read_for_query", return_value="extract"
        ) as mock_read:
            self._fetch_html(_manual_markdown(), query="the cap", part=2)

        self.assertEqual(mock_read.call_args.kwargs["part"], 2)

    def test_without_a_query_the_result_is_what_it_was_before_the_feature(self):
        with patch("workspace.ai.services.web.read_for_query") as mock_read:
            text = self._fetch_html(_manual_markdown())

        mock_read.assert_not_called()
        self.assertEqual(text, self._fetch_html(_manual_markdown()))

    def test_the_extract_is_built_for_the_room_the_link_list_leaves_it(self):
        # The reading step aims at the budget the composition will grant it.
        # Built for the whole budget instead, an extract on a page carrying a
        # link list overruns it, and the truncation lands on what the extract
        # puts last: the note naming what the page does not answer.
        markdown = (
            _manual_markdown()
            + "\n\n## See also\n\n"
            + " ".join(
                f"[reference number {i} of the manual](/ref/{i})" for i in range(12)
            )
        )

        text = self._fetch_html(
            markdown,
            query="what happens at the cap?",
            says=tuple(f"Rule {i}: the cap is 600 a minute." for i in range(40)),
            missing="It never says how long a ban lasts.",
            max_chars=1800,
        )

        self.assertIn("Not on this page: It never says how long a ban lasts.", text)
        self.assertNotIn("truncated at", text)
        self.assertLessEqual(len(text), 1800)

    def test_a_scoped_read_still_honours_the_budget(self):
        text = self._fetch_html(
            _manual_markdown(),
            query="what happens at the cap?",
            says=(_ANSWER,),
            max_chars=900,
        )

        self.assertLessEqual(len(text), 900)

    def _fetch_pdf(self, data, *, query="", says=(), max_chars=900):
        resp = _fake_response(
            url="https://example.com/doc.pdf", content_type="application/pdf"
        )
        resp.content = data
        with (
            patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls,
            patch(
                "workspace.ai.services.reading.call_llm_structured",
                side_effect=_extracting(*says),
            ),
        ):
            mock_client_cls.return_value = _client_returning(resp)
            return fetch_and_extract(
                "https://example.com/doc.pdf", max_chars=max_chars, query=query
            )

    def test_pdf_is_read_for_the_query_too(self):
        answer = "The daemon listens on port 8443 by default"
        pages = [f"Filler page {i} about nothing in particular" for i in range(30)]
        pages.insert(25, answer)

        text = self._fetch_pdf(
            make_pdf(pages), query="which port does it listen on?", says=(answer,)
        )

        self.assertIn("port 8443", text)
        self.assertNotIn("Filler page 0 ", text)

    def _fetch_feed(self, body, *, query="", says=(), max_chars=900):
        with (
            patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls,
            patch(
                "workspace.ai.services.reading.call_llm_structured",
                side_effect=_extracting(*says),
            ),
        ):
            mock_client_cls.return_value = _client_returning(
                _fake_response(
                    text=body,
                    url="https://example.com/feed.xml",
                    content_type="application/rss+xml",
                )
            )
            return fetch_and_extract(
                "https://example.com/feed.xml", max_chars=max_chars, query=query
            )

    def test_feed_is_read_for_the_query_too(self):
        items = "".join(
            f"<item><title>Gardening tips, part {i}</title>"
            f"<link>/posts/{i}</link></item>"
            for i in range(30)
        )
        rss = (
            "<?xml version='1.0'?><rss version='2.0'><channel>"
            "<title>The Blog</title>"
            "<item><title>Mounting a share over WebDAV</title>"
            "<link>/posts/webdav</link></item>" + items + "</channel></rss>"
        )

        text = self._fetch_feed(
            rss,
            query="has anything been written about network shares?",
            says=(
                "- [Mounting a share over WebDAV](https://example.com/posts/webdav)",
            ),
        )

        self.assertIn("Mounting a share over WebDAV", text)
        self.assertNotIn("Gardening tips, part 20]", text)


class PagedFetchTests(TestCase):
    """A page too long for one result, read one part at a time."""

    MAX_CHARS = 1000

    def _fetch(self, markdown, *, part=1):
        with (
            patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls,
            patch("workspace.ai.services.web.trafilatura.extract") as mock_extract,
        ):
            mock_client_cls.return_value = _client_returning(
                _fake_response(text="<html/>", url="https://example.com/page")
            )
            mock_extract.return_value = markdown
            return fetch_and_extract(
                "https://example.com/page", max_chars=self.MAX_CHARS, part=part
            )

    def _page(self):
        # Numbered throughout, so a gap or an overlap between two parts shows
        # up in the joined result instead of hiding in filler.
        return "".join(f"{i:06d}" for i in range(3000))

    def _read_to_the_end(self, fetch):
        """Every part of a page, walked the way the markers say to walk it."""
        parts = []
        while "end of the page" not in (text := fetch(len(parts) + 1)):
            parts.append(text)
            self.assertIn(f"part={len(parts) + 1}", text)
        parts.append(text)
        return parts

    def _stretch(self, part_text):
        """The stretch of the page a part carries: no header, no marker."""
        body = part_text.partition("\n\n")[2]
        return body.rpartition("\n\n[…")[0] or body

    def test_the_parts_cover_the_page_with_no_gap_and_no_overlap(self):
        page = self._page()

        parts = self._read_to_the_end(lambda part: self._fetch(page, part=part))

        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(self._stretch(part) for part in parts), page)

    def test_a_part_says_which_one_it_is_and_which_one_follows(self):
        parts = self._read_to_the_end(lambda part: self._fetch(self._page(), part=part))

        self.assertIn(f"part 1 of {len(parts)}", parts[0])
        self.assertIn("read this URL again with part=2", parts[0])
        self.assertIn(f"end of the page — part {len(parts)} of {len(parts)}", parts[-1])
        self.assertNotIn("read this URL again", parts[-1])

    def test_every_part_holds_to_the_budget(self):
        page = self._page()

        for part in (1, 2, 3):
            self.assertLessEqual(len(self._fetch(page, part=part)), self.MAX_CHARS)

    def test_every_part_names_the_page_it_comes_from(self):
        text = self._fetch(self._page(), part=3)

        self.assertIn("Source: https://example.com/page", text)

    def test_a_part_the_page_does_not_have_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._fetch(self._page(), part=999)

        self.assertIn("there is no part 999", str(ctx.exception))

    def test_a_part_below_the_first_is_refused(self):
        # Parts are numbered from one, and a slice taken from zero reads the
        # page backwards.
        with self.assertRaises(ValueError) as ctx:
            self._fetch(self._page(), part=0)

        self.assertIn("there is no part 0", str(ctx.exception))

    def test_a_page_that_fits_is_a_single_part(self):
        short = "short enough to be read in one go"

        self.assertNotIn("part 1 of", self._fetch(short))
        with self.assertRaises(ValueError) as ctx:
            self._fetch(short, part=2)
        self.assertIn("This page has 1 part", str(ctx.exception))

    def test_a_part_holds_to_a_budget_too_small_to_carry_a_marker(self):
        # Nothing composes the JSON path afterwards, so the room the marker is
        # given is the only thing keeping a part inside the budget.
        resp = _fake_response(text="{}", content_type="application/json")
        resp.json.return_value = {"body": "B" * 5000}
        with patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls:
            mock_client_cls.return_value = _client_returning(resp)
            text = fetch_and_extract("https://example.com/api", max_chars=120)

        self.assertLessEqual(len(text), 120)

    def test_json_is_paged_too(self):
        payload = {"body": "".join(f"{i:06d}" for i in range(400))}

        def fetch(part):
            resp = _fake_response(text="{}", content_type="application/json")
            resp.json.return_value = payload
            with patch("workspace.ai.services.web.httpx2.Client") as mock_client_cls:
                mock_client_cls.return_value = _client_returning(resp)
                return fetch_and_extract(
                    "https://example.com/api", max_chars=800, part=part
                )

        parts = self._read_to_the_end(fetch)

        self.assertGreater(len(parts), 1)
        self.assertEqual(
            "".join(part.rpartition("\n\n[…")[0] for part in parts),
            json.dumps(payload, separators=(",", ":")),
        )


class ReadWebpageToolTests(TestCase):
    def _run(self, **kwargs):
        provider = WebToolProvider()
        return provider.read_webpage(ReadWebpageParams(**kwargs), None, None, None, {})

    def test_the_query_reaches_the_service(self):
        with patch(
            "workspace.ai.services.web.fetch_and_extract", return_value="page"
        ) as mock_fetch:
            self._run(url=" https://example.com/manual ", query="  rate limit  ")

        self.assertEqual(mock_fetch.call_args.args, ("https://example.com/manual",))
        self.assertEqual(
            mock_fetch.call_args.kwargs, {"query": "rate limit", "part": 1}
        )

    def test_omitting_the_query_reads_the_whole_page(self):
        with patch(
            "workspace.ai.services.web.fetch_and_extract", return_value="page"
        ) as mock_fetch:
            self._run(url="https://example.com/manual")

        self.assertEqual(mock_fetch.call_args.kwargs, {"query": "", "part": 1})

    def test_the_part_reaches_the_service(self):
        with patch(
            "workspace.ai.services.web.fetch_and_extract", return_value="page"
        ) as mock_fetch:
            self._run(url="https://example.com/manual", query="rate limit", part=3)

        self.assertEqual(mock_fetch.call_args.kwargs["part"], 3)

    def test_a_part_the_page_does_not_have_comes_back_as_an_error(self):
        with patch(
            "workspace.ai.services.web.fetch_and_extract",
            side_effect=ValueError("This page has 2 parts — there is no part 5."),
        ):
            result = self._run(url="https://example.com/manual", part=5)

        self.assertEqual(result, "Error: This page has 2 parts — there is no part 5.")

    def test_a_blank_url_is_refused(self):
        self.assertTrue(self._run(url="   ").startswith("Error:"))
