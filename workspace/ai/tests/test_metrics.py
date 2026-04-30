"""Tests for Prometheus instrumentation in the AI module.

Targets the call sites where the LLM/image SDK is invoked:
- workspace.ai.tasks._call_llm  → ai_request_duration_seconds, ai_tokens_total
- workspace.ai.tools.GenerateImageTool → ai_image_requests_total
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from prometheus_client import REGISTRY


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
)
class CallLlmMetricsTests(TestCase):
    def _make_response(self, model='gpt-4o-mini', prompt_tokens=10, completion_tokens=5):
        choice = MagicMock(message=MagicMock(content='hi', tool_calls=None))
        usage = MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return MagicMock(choices=[choice], model=model, usage=usage)

    @patch('workspace.ai.client.get_ai_client')
    def test_successful_call_records_duration_and_tokens(self, mock_get_client):
        client = MagicMock()
        client.chat.completions.create.return_value = self._make_response(
            prompt_tokens=42, completion_tokens=7,
        )
        mock_get_client.return_value = client

        from workspace.ai.tasks import _call_llm

        before_ok = _sample(
            'ai_request_duration_seconds_count',
            {'model': 'gpt-4o-mini', 'status': 'ok'},
        )
        before_prompt = _sample(
            'ai_tokens_total', {'model': 'gpt-4o-mini', 'kind': 'prompt'},
        )
        before_completion = _sample(
            'ai_tokens_total', {'model': 'gpt-4o-mini', 'kind': 'completion'},
        )

        _call_llm(messages=[{'role': 'user', 'content': 'hi'}])

        self.assertEqual(
            _sample(
                'ai_request_duration_seconds_count',
                {'model': 'gpt-4o-mini', 'status': 'ok'},
            ) - before_ok,
            1,
        )
        self.assertEqual(
            _sample('ai_tokens_total', {'model': 'gpt-4o-mini', 'kind': 'prompt'})
            - before_prompt,
            42,
        )
        self.assertEqual(
            _sample('ai_tokens_total', {'model': 'gpt-4o-mini', 'kind': 'completion'})
            - before_completion,
            7,
        )

    @patch('workspace.ai.client.get_ai_client')
    def test_api_error_observes_duration_with_error_status_and_reraises(self, mock_get_client):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError('boom')
        mock_get_client.return_value = client

        from workspace.ai.tasks import _call_llm

        before_err = _sample(
            'ai_request_duration_seconds_count',
            {'model': 'gpt-4o-mini', 'status': 'error'},
        )
        with self.assertRaises(RuntimeError):
            _call_llm(messages=[{'role': 'user', 'content': 'hi'}])

        self.assertEqual(
            _sample(
                'ai_request_duration_seconds_count',
                {'model': 'gpt-4o-mini', 'status': 'error'},
            ) - before_err,
            1,
        )

    @patch('workspace.ai.client.get_ai_client')
    def test_zero_tokens_does_not_create_a_zero_sample(self, mock_get_client):
        # When usage reports 0 tokens for a kind, we skip the .inc() so the
        # series isn't materialized — keeps the /metrics surface clean.
        client = MagicMock()
        client.chat.completions.create.return_value = self._make_response(
            prompt_tokens=10, completion_tokens=0,
        )
        mock_get_client.return_value = client

        from workspace.ai.tasks import _call_llm

        labels_completion = {'model': 'gpt-4o-mini', 'kind': 'completion'}
        before = _sample('ai_tokens_total', labels_completion)
        _call_llm(messages=[{'role': 'user', 'content': 'x'}])
        # Counter not bumped for the zero-token kind.
        self.assertEqual(_sample('ai_tokens_total', labels_completion), before)


@override_settings(
    AI_API_KEY='test-key',
    AI_IMAGE_MODEL='dall-e-3',
)
class ImageRequestMetricsTests(TestCase):
    @patch('workspace.ai.tools.get_image_client')
    def test_successful_generate_increments_ok_counter(self, mock_get_client):
        import base64

        from workspace.ai.tools import GenerateImageParams, ImageToolProvider

        client = MagicMock()
        response = MagicMock()
        response.data = [MagicMock(b64_json=base64.b64encode(b'fake').decode())]
        client.images.generate.return_value = response
        mock_get_client.return_value = client

        labels = {'model': 'dall-e-3', 'op': 'generate', 'status': 'ok'}
        before = _sample('ai_image_requests_total', labels)

        ImageToolProvider().generate_image(
            GenerateImageParams(prompt='a cat'),
            user=None, bot=None, conversation_id='conv-1', context={},
        )

        self.assertEqual(_sample('ai_image_requests_total', labels) - before, 1)

    @patch('workspace.ai.tools.get_image_client')
    def test_generate_error_increments_error_counter(self, mock_get_client):
        from workspace.ai.tools import GenerateImageParams, ImageToolProvider

        client = MagicMock()
        client.images.generate.side_effect = RuntimeError('upstream down')
        mock_get_client.return_value = client

        labels = {'model': 'dall-e-3', 'op': 'generate', 'status': 'error'}
        before = _sample('ai_image_requests_total', labels)

        result = ImageToolProvider().generate_image(
            GenerateImageParams(prompt='a cat'),
            user=None, bot=None, conversation_id='conv-1', context={},
        )

        self.assertTrue(result.startswith('Error'))
        self.assertEqual(_sample('ai_image_requests_total', labels) - before, 1)

    @patch('workspace.ai.services.image.get_image_client')
    def test_successful_edit_increments_ok_counter(self, mock_get_client):
        # Mock the OpenAI-compatible endpoint to succeed on the first try.
        import base64

        from workspace.ai.services.image import ai_edit_image

        client = MagicMock()
        response = MagicMock()
        response.data = [MagicMock(b64_json=base64.b64encode(b'edited').decode())]
        client.images.edit.return_value = response
        mock_get_client.return_value = client

        labels = {'model': 'dall-e-3', 'op': 'edit', 'status': 'ok'}
        before = _sample('ai_image_requests_total', labels)

        result = ai_edit_image(b'source-bytes', 'make it blue')

        self.assertEqual(result, b'edited')
        self.assertEqual(_sample('ai_image_requests_total', labels) - before, 1)

    @patch('workspace.ai.services.image._edit_via_ollama')
    @patch('workspace.ai.services.image.get_image_client')
    def test_edit_error_increments_error_counter_after_both_backends_fail(
        self, mock_get_client, mock_ollama,
    ):
        from workspace.ai.services.image import ai_edit_image

        # OpenAI path raises, Ollama fallback also raises → final RuntimeError.
        client = MagicMock()
        client.images.edit.side_effect = RuntimeError('openai down')
        mock_get_client.return_value = client
        mock_ollama.side_effect = RuntimeError('ollama down')

        labels = {'model': 'dall-e-3', 'op': 'edit', 'status': 'error'}
        before = _sample('ai_image_requests_total', labels)

        with self.assertRaises(RuntimeError):
            ai_edit_image(b'source-bytes', 'make it red')

        self.assertEqual(_sample('ai_image_requests_total', labels) - before, 1)
