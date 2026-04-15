from unittest.mock import patch

from django.test import TestCase, override_settings

from workspace.ai.client import get_ai_client, get_image_client, is_ai_enabled
from workspace.ai.services.image import VALID_SIZES, ai_edit_image


# ── client.py ───────────────────────────────────────────────────

class IsAiEnabledTests(TestCase):

    @override_settings(AI_API_KEY='sk-test')
    def test_enabled_when_key_set(self):
        self.assertTrue(is_ai_enabled())

    @override_settings(AI_API_KEY='')
    def test_disabled_when_key_empty(self):
        self.assertFalse(is_ai_enabled())


class GetAiClientTests(TestCase):

    @override_settings(AI_API_KEY='', AI_BASE_URL='http://localhost', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_returns_none_when_no_key(self):
        self.assertIsNone(get_ai_client())

    @override_settings(AI_API_KEY='sk-test', AI_BASE_URL='http://localhost', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_returns_client_when_key_set(self):
        client = get_ai_client()
        self.assertIsNotNone(client)


class GetImageClientTests(TestCase):

    @override_settings(AI_API_KEY='', AI_BASE_URL='http://localhost', AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_returns_none_when_no_key(self):
        self.assertIsNone(get_image_client())

    @override_settings(AI_API_KEY='sk-test', AI_BASE_URL='http://localhost', AI_IMAGE_BASE_URL='http://img.localhost', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_uses_image_base_url_when_set(self):
        client = get_image_client()
        self.assertIsNotNone(client)

    @override_settings(AI_API_KEY='sk-test', AI_BASE_URL='http://localhost', AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_falls_back_to_ai_base_url(self):
        client = get_image_client()
        self.assertIsNotNone(client)


# ── image_service.py ────────────────────────────────────────────

class AiEditImageTests(TestCase):

    @override_settings(AI_API_KEY='', AI_BASE_URL='', AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2)
    def test_raises_when_ai_not_configured(self):
        with self.assertRaises(ValueError) as ctx:
            ai_edit_image(b'img', 'make it blue')
        self.assertIn('not configured', str(ctx.exception))

    def test_raises_when_prompt_empty(self):
        with self.assertRaises(ValueError) as ctx:
            ai_edit_image(b'img', '')
        self.assertIn('prompt', str(ctx.exception).lower())

    def test_raises_when_prompt_whitespace(self):
        with self.assertRaises(ValueError):
            ai_edit_image(b'img', '   ')

    @override_settings(
        AI_API_KEY='sk-test', AI_BASE_URL='http://localhost',
        AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2,
        AI_IMAGE_MODEL='test-model',
    )
    @patch('workspace.ai.services.image._edit_via_openai')
    def test_calls_openai_backend(self, mock_openai):
        mock_openai.return_value = b'edited-image'
        result = ai_edit_image(b'source', 'make it red')
        self.assertEqual(result, b'edited-image')
        mock_openai.assert_called_once()

    @override_settings(
        AI_API_KEY='sk-test', AI_BASE_URL='http://localhost',
        AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2,
        AI_IMAGE_MODEL='test-model',
    )
    @patch('workspace.ai.services.image._edit_via_ollama')
    @patch('workspace.ai.services.image._edit_via_openai')
    def test_falls_back_to_ollama(self, mock_openai, mock_ollama):
        mock_openai.side_effect = Exception('OpenAI failed')
        mock_ollama.return_value = b'ollama-image'
        result = ai_edit_image(b'source', 'edit')
        self.assertEqual(result, b'ollama-image')

    @override_settings(
        AI_API_KEY='sk-test', AI_BASE_URL='http://localhost',
        AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2,
        AI_IMAGE_MODEL='test-model',
    )
    @patch('workspace.ai.services.image._edit_via_ollama')
    @patch('workspace.ai.services.image._edit_via_openai')
    def test_raises_when_both_backends_fail(self, mock_openai, mock_ollama):
        mock_openai.side_effect = Exception('OpenAI failed')
        mock_ollama.side_effect = Exception('Ollama failed')
        with self.assertRaises(RuntimeError):
            ai_edit_image(b'source', 'edit')

    @override_settings(
        AI_API_KEY='sk-test', AI_BASE_URL='http://localhost',
        AI_IMAGE_BASE_URL='', AI_TIMEOUT=30, AI_MAX_RETRIES=2,
        AI_IMAGE_MODEL='test-model',
    )
    @patch('workspace.ai.services.image._edit_via_openai')
    def test_invalid_size_defaults_to_1024(self, mock_openai):
        mock_openai.return_value = b'img'
        ai_edit_image(b'source', 'edit', size='999x999')
        call_args = mock_openai.call_args
        self.assertEqual(call_args[0][3], '1024x1024')

    def test_valid_sizes_constant(self):
        self.assertIn('1024x1024', VALID_SIZES)
        self.assertIn('1792x1024', VALID_SIZES)
        self.assertIn('1024x1792', VALID_SIZES)
