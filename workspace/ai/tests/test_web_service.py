from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from workspace.ai.web_service import _is_url_safe, fetch_and_extract, search


class IsUrlSafeTests(TestCase):

    def test_localhost_blocked(self):
        self.assertFalse(_is_url_safe('http://localhost/admin'))

    def test_127_blocked(self):
        self.assertFalse(_is_url_safe('http://127.0.0.1:8000/secret'))

    def test_private_10_blocked(self):
        self.assertFalse(_is_url_safe('http://10.0.0.1/internal'))

    def test_private_192_blocked(self):
        self.assertFalse(_is_url_safe('http://192.168.1.1/router'))

    def test_private_172_blocked(self):
        self.assertFalse(_is_url_safe('http://172.16.0.1/internal'))

    def test_public_url_allowed(self):
        self.assertTrue(_is_url_safe('https://example.com/page'))

    def test_public_ip_allowed(self):
        self.assertTrue(_is_url_safe('http://8.8.8.8/'))

    def test_ipv6_loopback_blocked(self):
        self.assertFalse(_is_url_safe('http://[::1]/'))

    @override_settings(SEARXNG_BLOCKED_DOMAINS='evil.com,spam.org')
    def test_blocked_domain_exact(self):
        self.assertFalse(_is_url_safe('https://evil.com/page'))

    @override_settings(SEARXNG_BLOCKED_DOMAINS='evil.com')
    def test_blocked_domain_subdomain(self):
        self.assertFalse(_is_url_safe('https://sub.evil.com/page'))

    @override_settings(SEARXNG_BLOCKED_DOMAINS='evil.com')
    def test_blocked_domain_allows_other(self):
        self.assertTrue(_is_url_safe('https://example.com/page'))

    @override_settings(SEARXNG_BLOCKED_DOMAINS='Evil.COM')
    def test_blocked_domain_case_insensitive(self):
        self.assertFalse(_is_url_safe('https://EVIL.com/page'))

    @override_settings(SEARXNG_BLOCKED_DOMAINS='')
    def test_empty_blocklist_allows_all(self):
        self.assertTrue(_is_url_safe('https://anything.com/'))


@override_settings(SEARXNG_URL='http://searxng:8080')
class SearchTests(TestCase):

    @patch('workspace.ai.web_service.httpx.Client')
    def test_search_returns_results(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'results': [
                {'title': 'Result 1', 'url': 'https://a.com', 'content': 'Snippet 1'},
                {'title': 'Result 2', 'url': 'https://b.com', 'content': 'Snippet 2'},
            ],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = search('test query', max_results=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['title'], 'Result 1')
        self.assertEqual(results[1]['url'], 'https://b.com')

    @override_settings(SEARXNG_URL='')
    def test_search_disabled_when_no_url(self):
        results = search('test')
        self.assertEqual(results, [])

    @patch('workspace.ai.web_service.httpx.Client')
    def test_search_handles_error(self, mock_client_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError('Connection refused')
        mock_client_cls.return_value = mock_client

        results = search('failing query')

        self.assertEqual(results, [])


class FetchAndExtractTests(TestCase):

    def test_private_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fetch_and_extract('http://localhost:8000/admin/')
        self.assertIn('private', str(ctx.exception))

    @patch('workspace.ai.web_service.trafilatura.extract')
    @patch('workspace.ai.web_service.httpx.Client')
    def test_extracts_content(self, mock_client_cls, mock_extract):
        mock_resp = MagicMock()
        mock_resp.text = '<html><body><p>Hello world</p></body></html>'
        mock_resp.content = b'x' * 100
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        mock_extract.return_value = 'Hello world'

        text = fetch_and_extract('https://example.com/article')

        self.assertEqual(text, 'Hello world')
        mock_extract.assert_called_once()

    @patch('workspace.ai.web_service.trafilatura.extract')
    @patch('workspace.ai.web_service.httpx.Client')
    def test_truncates_long_content(self, mock_client_cls, mock_extract):
        mock_resp = MagicMock()
        mock_resp.text = '<html><body>long</body></html>'
        mock_resp.content = b'x' * 100
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        mock_extract.return_value = 'A' * 10000

        text = fetch_and_extract('https://example.com/', max_chars=100)

        self.assertEqual(len(text), 100 + len('\n\n[… truncated]'))
        self.assertTrue(text.endswith('[… truncated]'))

    @patch('workspace.ai.web_service.trafilatura.extract')
    @patch('workspace.ai.web_service.httpx.Client')
    def test_fallback_when_trafilatura_returns_empty(self, mock_client_cls, mock_extract):
        mock_resp = MagicMock()
        mock_resp.text = '<html><body><p>Fallback text</p></body></html>'
        mock_resp.content = b'x' * 100
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        mock_extract.return_value = None  # trafilatura failed

        text = fetch_and_extract('https://example.com/')

        self.assertIn('Fallback text', text)

    @patch('workspace.ai.web_service.httpx.Client')
    def test_rejects_oversized_response(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.content = b'x' * (3 * 1024 * 1024)  # 3 MB
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with self.assertRaises(ValueError) as ctx:
            fetch_and_extract('https://example.com/huge')
        self.assertIn('too large', str(ctx.exception))
