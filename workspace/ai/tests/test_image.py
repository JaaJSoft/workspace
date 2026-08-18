import base64
from unittest.mock import MagicMock, patch

import httpx2
from django.test import SimpleTestCase, override_settings
from openai import BadRequestError, InternalServerError, RateLimitError

from workspace.ai.services.image import (
    ImageGenerationError,
    ai_edit_image,
    ai_generate_image,
)

PNG_B64 = base64.b64encode(b"\x89PNG image").decode()


def _image_response(b64=PNG_B64):
    response = MagicMock()
    response.data = [MagicMock(b64_json=b64)]
    return response


def _empty_response():
    response = MagicMock()
    response.data = []
    return response


def _api_error(cls, status_code):
    request = httpx2.Request("POST", "http://image-backend/v1/images/generations")
    response = httpx2.Response(status_code, request=request)
    return cls("backend said no", response=response, body=None)


@override_settings(
    AI_API_KEY="test-key",
    AI_IMAGE_MODEL="test-image-model",
    AI_IMAGE_MAX_ATTEMPTS=3,
    AI_IMAGE_RETRY_DELAY=0,
)
class GenerateImageRetryTests(SimpleTestCase):
    def _generate(self, client, prompt="a cat"):
        with patch("workspace.ai.services.image.get_image_client", return_value=client):
            return ai_generate_image(prompt)

    def test_retries_until_the_backend_answers(self):
        client = MagicMock()
        client.images.generate.side_effect = [
            RuntimeError("upstream down"),
            RuntimeError("upstream down"),
            _image_response(),
        ]

        self.assertEqual(self._generate(client), b"\x89PNG image")
        self.assertEqual(client.images.generate.call_count, 3)

    def test_retries_a_response_carrying_no_image(self):
        # A 200 with an empty payload is the failure mode that hurts most:
        # nothing raises, so without a retry it reads as a finished call.
        client = MagicMock()
        client.images.generate.side_effect = [_empty_response(), _image_response()]

        self.assertEqual(self._generate(client), b"\x89PNG image")
        self.assertEqual(client.images.generate.call_count, 2)

    def test_retries_a_malformed_payload(self):
        client = MagicMock()
        client.images.generate.side_effect = [
            _image_response(b64="!!!not-base64!!!"),
            _image_response(),
        ]

        self.assertEqual(self._generate(client), b"\x89PNG image")
        self.assertEqual(client.images.generate.call_count, 2)

    def test_gives_up_after_the_attempt_budget(self):
        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("upstream down")

        with self.assertRaises(ImageGenerationError) as ctx:
            self._generate(client)

        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(client.images.generate.call_count, 3)

    def test_retries_a_rate_limited_request(self):
        client = MagicMock()
        client.images.generate.side_effect = [
            _api_error(RateLimitError, 429),
            _image_response(),
        ]

        self.assertEqual(self._generate(client), b"\x89PNG image")
        self.assertEqual(client.images.generate.call_count, 2)

    def test_retries_an_upstream_server_error(self):
        client = MagicMock()
        client.images.generate.side_effect = [
            _api_error(InternalServerError, 503),
            _image_response(),
        ]

        self.assertEqual(self._generate(client), b"\x89PNG image")
        self.assertEqual(client.images.generate.call_count, 2)

    def test_does_not_retry_a_rejected_prompt(self):
        # A 400 is a verdict on the prompt, not a hiccup: replaying it would
        # burn the budget (and the wall clock) for the same answer.
        client = MagicMock()
        client.images.generate.side_effect = _api_error(BadRequestError, 400)

        with self.assertRaises(ImageGenerationError) as ctx:
            self._generate(client)

        self.assertEqual(ctx.exception.attempts, 1)
        self.assertTrue(ctx.exception.rejected)
        self.assertEqual(client.images.generate.call_count, 1)

    def test_an_unreachable_backend_is_not_reported_as_a_rejection(self):
        # The flag decides whether the caller asks the model to rewrite its
        # prompt: a prompt the service never read must not take the blame.
        client = MagicMock()
        client.images.generate.side_effect = _api_error(InternalServerError, 503)

        with self.assertRaises(ImageGenerationError) as ctx:
            self._generate(client)

        self.assertFalse(ctx.exception.rejected)

    @override_settings(AI_IMAGE_RETRY_DELAY=2)
    def test_backoff_doubles_between_attempts(self):
        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("upstream down")

        with patch("workspace.ai.services.image.time.sleep") as mock_sleep:
            with self.assertRaises(ImageGenerationError):
                self._generate(client)

        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [2, 4])

    def test_empty_prompt_is_rejected_without_calling_the_backend(self):
        client = MagicMock()

        with self.assertRaises(ValueError):
            self._generate(client, prompt="   ")

        client.images.generate.assert_not_called()


@override_settings(
    AI_API_KEY="test-key",
    AI_IMAGE_MODEL="test-image-model",
    AI_IMAGE_MAX_ATTEMPTS=3,
    AI_IMAGE_RETRY_DELAY=0,
)
class EditImageRetryTests(SimpleTestCase):
    def _edit(self, client):
        with patch("workspace.ai.services.image.get_image_client", return_value=client):
            return ai_edit_image(b"source", "make it blue")

    def test_retries_when_both_backends_fail(self):
        client = MagicMock()
        client.images.edit.side_effect = [
            RuntimeError("openai down"),
            _image_response(),
        ]

        with patch(
            "workspace.ai.services.image._edit_via_ollama",
            side_effect=RuntimeError("ollama down"),
        ):
            self.assertEqual(self._edit(client), b"\x89PNG image")

        self.assertEqual(client.images.edit.call_count, 2)

    def test_gives_up_after_the_attempt_budget(self):
        client = MagicMock()
        client.images.edit.side_effect = RuntimeError("openai down")

        with patch(
            "workspace.ai.services.image._edit_via_ollama",
            side_effect=RuntimeError("ollama down"),
        ):
            with self.assertRaises(ImageGenerationError) as ctx:
                self._edit(client)

        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(client.images.edit.call_count, 3)

    def test_does_not_retry_a_rejected_prompt(self):
        # The rejection reaches the retry layer wrapped in the fallback
        # backend's own error; it must still be recognized as final.
        client = MagicMock()
        client.images.edit.side_effect = _api_error(BadRequestError, 400)

        with patch(
            "workspace.ai.services.image._edit_via_ollama",
            side_effect=RuntimeError("ollama down"),
        ):
            with self.assertRaises(ImageGenerationError) as ctx:
                self._edit(client)

        self.assertEqual(ctx.exception.attempts, 1)
        self.assertTrue(ctx.exception.rejected)
        self.assertEqual(client.images.edit.call_count, 1)
