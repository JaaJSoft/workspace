from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from workspace.ai.services.web import (
    _absolutize_links,
    _is_url_safe,
    fetch_and_extract,
    search,
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

        self.assertTrue(text.startswith("A" * 100))
        self.assertIn("the page continues", text)

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
