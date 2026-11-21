"""Tests for Prometheus instrumentation in the AI module.

- every model request -> ai_request_duration_seconds, ai_tokens_total
- the image backend -> ai_image_requests_total
- a bot's tool loop -> ai_tool_calls_total, ai_tool_rounds,
  ai_tool_loop_stops_total
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from prometheus_client import REGISTRY

from workspace.ai.prompts.base import prompt
from workspace.ai.services.llm import run_prompt

from .agents import BotRunCase, call, reply, scripted, tool_context, write_call


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class _Change:
    """How much a sample moved while the block ran."""

    def __init__(self, name, **labels):
        self._read = lambda: _sample(name, labels)

    def __enter__(self):
        self._before = self._read()
        return self

    def __exit__(self, *exc):
        self.by = self._read() - self._before


class ModelRequestMetricsTests(SimpleTestCase):
    def test_a_request_records_its_duration_and_its_tokens(self):
        ok = _Change("ai_request_duration_seconds_count", model="m-ok", status="ok")
        sent = _Change("ai_tokens_total", model="m-ok", kind="prompt")
        written = _Change("ai_tokens_total", model="m-ok", kind="completion")
        with ok, sent, written:
            with scripted(reply("hi", input_tokens=42, output_tokens=7), model="m-ok"):
                run_prompt(prompt("be brief", "hi"))

        self.assertEqual((ok.by, sent.by, written.by), (1, 42, 7))

    def test_a_failed_request_is_timed_as_an_error_and_still_raises(self):
        def down(messages, tools):
            raise RuntimeError("upstream down")

        failed = _Change(
            "ai_request_duration_seconds_count", model="m-down", status="error"
        )
        with failed:
            with scripted(down, model="m-down"):
                with self.assertRaisesMessage(RuntimeError, "upstream down"):
                    run_prompt(prompt("be brief", "hi"))

        self.assertEqual(failed.by, 1)


@override_settings(
    AI_API_KEY="test-key",
    AI_IMAGE_MODEL="dall-e-3",
    AI_IMAGE_MAX_ATTEMPTS=3,
    AI_IMAGE_RETRY_DELAY=0,
)
class ImageRequestMetricsTests(TestCase):
    @patch("workspace.ai.services.image.get_image_client")
    def test_successful_generate_increments_ok_counter(self, mock_get_client):
        import base64

        from workspace.ai.tools import GenerateImageParams, ImageToolProvider

        client = MagicMock()
        response = MagicMock()
        response.data = [MagicMock(b64_json=base64.b64encode(b"fake").decode())]
        client.images.generate.return_value = response
        mock_get_client.return_value = client

        labels = {"model": "dall-e-3", "op": "generate", "status": "ok"}
        before = _sample("ai_image_requests_total", labels)

        ImageToolProvider().generate_image(
            tool_context(conversation_id="conv-1"), GenerateImageParams(prompt="a cat")
        )

        self.assertEqual(_sample("ai_image_requests_total", labels) - before, 1)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_error_increments_error_counter(self, mock_get_client):
        from workspace.ai.tool_registry import ToolError
        from workspace.ai.tools import GenerateImageParams, ImageToolProvider

        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("upstream down")
        mock_get_client.return_value = client

        labels = {"model": "dall-e-3", "op": "generate", "status": "error"}
        before = _sample("ai_image_requests_total", labels)

        with self.assertRaises(ToolError):
            ImageToolProvider().generate_image(
                tool_context(conversation_id="conv-1"),
                GenerateImageParams(prompt="a cat"),
            )

        # One sample per attempt: the counter tracks calls to the backend,
        # and the retries are real calls.
        self.assertEqual(_sample("ai_image_requests_total", labels) - before, 3)

    @patch("workspace.ai.services.image.get_image_client")
    def test_successful_edit_increments_ok_counter(self, mock_get_client):
        # Mock the OpenAI-compatible endpoint to succeed on the first try.
        import base64

        from workspace.ai.services.image import ai_edit_image

        client = MagicMock()
        response = MagicMock()
        response.data = [MagicMock(b64_json=base64.b64encode(b"edited").decode())]
        client.images.edit.return_value = response
        mock_get_client.return_value = client

        labels = {"model": "dall-e-3", "op": "edit", "status": "ok"}
        before = _sample("ai_image_requests_total", labels)

        result = ai_edit_image(b"source-bytes", "make it blue")

        self.assertEqual(result, b"edited")
        self.assertEqual(_sample("ai_image_requests_total", labels) - before, 1)

    @patch("workspace.ai.services.image._edit_via_ollama")
    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_error_increments_error_counter_after_both_backends_fail(
        self,
        mock_get_client,
        mock_ollama,
    ):
        from workspace.ai.services.image import ai_edit_image

        # OpenAI path raises, Ollama fallback also raises → final RuntimeError.
        client = MagicMock()
        client.images.edit.side_effect = RuntimeError("openai down")
        mock_get_client.return_value = client
        mock_ollama.side_effect = RuntimeError("ollama down")

        labels = {"model": "dall-e-3", "op": "edit", "status": "error"}
        before = _sample("ai_image_requests_total", labels)

        with self.assertRaises(RuntimeError):
            ai_edit_image(b"source-bytes", "make it red")

        self.assertEqual(_sample("ai_image_requests_total", labels) - before, 3)


class ToolLoopMetricsTests(BotRunCase):
    """What a bot run counts: each call, its rounds, and why it was cut short."""

    def _calls(self, tool, status):
        return _Change("ai_tool_calls_total", tool=tool, status=status)

    def test_a_call_that_returns_counts_as_ok_for_its_tool(self):
        with self._calls("write_note", "ok") as counted:
            self.run_bot(reply("", write_call("c1")), reply("done"))

        self.assertEqual(counted.by, 1)

    def test_a_failing_tool_counts_as_an_error(self):
        with self._calls("fail", "error") as counted:
            self.run_bot(reply("", call("fail", {}, "c1")), reply("done"))

        self.assertEqual(counted.by, 1)

    def test_a_raising_tool_counts_as_an_error_and_still_propagates(self):
        with self._calls("explode", "error") as counted:
            with self.assertRaises(RuntimeError):
                self.run_bot(reply("", call("explode", {}, "c1")), repeat=True)

        self.assertEqual(counted.by, 1)

    def test_a_tool_the_model_invented_folds_into_one_series(self):
        # A model can invent any name, and a name is a label.
        with (
            self._calls("made_up", "error") as named,
            self._calls("unknown", "error") as folded,
        ):
            self.run_bot(reply("", call("made_up", {}, "c1")), reply("done"))

        self.assertEqual((named.by, folded.by), (0, 1))

    def test_a_call_the_schema_refuses_counts_as_an_error(self):
        with self._calls("read_webpage", "error") as counted:
            self.run_bot(
                reply("", call("read_webpage", {"url": 3}, "c1")), reply("done")
            )

        self.assertEqual(counted.by, 1)

    @override_settings(AI_MAX_IDENTICAL_TOOL_CALLS=1)
    def test_a_refused_duplicate_counts_as_a_repeat_and_stops_the_loop(self):
        def model(messages, tools):
            return reply("", write_call(f"c{len(messages)}")) if tools else reply("ok")

        stops = _Change("ai_tool_loop_stops_total", reason="repeat_loop")
        with self._calls("write_note", "repeat") as repeats, stops:
            self.run_bot(model, repeat=True)

        self.assertEqual((repeats.by, stops.by), (1, 1))

    @override_settings(AI_MAX_TOOL_ROUNDS=2, AI_MAX_IDENTICAL_TOOL_CALLS=10)
    def test_the_round_cap_is_counted_as_an_early_stop(self):
        def model(messages, tools):
            return reply("", write_call(f"c{len(messages)}")) if tools else reply("ok")

        with _Change("ai_tool_loop_stops_total", reason="round_cap") as stops:
            self.run_bot(model, repeat=True)

        self.assertEqual(stops.by, 1)

    def test_the_rounds_histogram_takes_one_sample_per_reply(self):
        runs = _Change("ai_tool_rounds_count", model="test-model")
        rounds = _Change("ai_tool_rounds_sum", model="test-model")
        with runs, rounds:
            self.run_bot(
                reply("", write_call("c1", "a")),
                reply("", write_call("c2", "b")),
                reply("done"),
            )

        self.assertEqual((runs.by, rounds.by), (1, 2))

    def test_a_reply_without_tools_is_a_sample_of_zero_rounds(self):
        runs = _Change("ai_tool_rounds_count", model="test-model")
        rounds = _Change("ai_tool_rounds_sum", model="test-model")
        with runs, rounds:
            self.run_bot(reply("hello"))

        self.assertEqual((runs.by, rounds.by), (1, 0))

    def test_a_cancelled_run_is_left_out_of_the_distribution(self):
        with _Change("ai_tool_rounds_count", model="test-model") as runs:
            self.run_bot(reply("hello"), is_cancelled=lambda: True)

        self.assertEqual(runs.by, 0)
