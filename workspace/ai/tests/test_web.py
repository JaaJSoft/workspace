import json
from typing import get_args
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from workspace.ai.services.web import (
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
    WebSearchParams,
    WebToolProvider,
)


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

        self.assertEqual(text, "Hello world")
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

        self.assertEqual(fetch_and_extract("https://example.com/x"), "not json")

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_truncates_long_content(self, mock_client_cls, mock_extract):
        mock_client_cls.return_value = _client_returning(_fake_response(text="<html/>"))
        mock_extract.return_value = "A" * 10000

        text = fetch_and_extract("https://example.com/", max_chars=100)

        # The marker counts against the budget, so the caller never gets more
        # than the number of characters it asked for.
        self.assertEqual(len(text), 100)
        self.assertTrue(text.startswith("A"))
        self.assertIn("the page continues", text)

    @patch("workspace.ai.services.web.trafilatura.extract")
    @patch("workspace.ai.services.web.httpx2.Client")
    def test_json_truncation_also_respects_the_budget(
        self, mock_client_cls, mock_extract
    ):
        resp = _fake_response(text="{}", content_type="application/json")
        resp.json.return_value = {"body": "B" * 5000}
        mock_client_cls.return_value = _client_returning(resp)

        text = fetch_and_extract("https://example.com/api", max_chars=200)

        self.assertEqual(len(text), 200)

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
        resp.content = b"x" * (3 * 1024 * 1024)  # 3 MB
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
