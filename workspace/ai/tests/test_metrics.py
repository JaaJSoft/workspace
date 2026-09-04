"""Tests for Prometheus instrumentation in the AI module.

Targets the call sites where the LLM/image SDK is invoked:
- workspace.ai.services.llm.call_llm  → ai_request_duration_seconds, ai_tokens_total
- workspace.ai.tools.GenerateImageTool → ai_image_requests_total
- workspace.ai.harness.observers.MetricsObserver → ai_tool_calls_total,
  ai_tool_rounds, ai_tool_loop_stops_total
"""

import itertools
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from prometheus_client import REGISTRY

from workspace.ai.harness.observers import MetricsObserver
from workspace.ai.harness.runner import StopReason

from .harness import ScriptedModel, StubToolset, build_runner, call, reply, tool_reply


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
)
class CallLlmMetricsTests(TestCase):
    def _make_response(
        self, model="gpt-4o-mini", prompt_tokens=10, completion_tokens=5
    ):
        choice = MagicMock(message=MagicMock(content="hi", tool_calls=None))
        usage = MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return MagicMock(choices=[choice], model=model, usage=usage)

    @patch("workspace.ai.client.get_ai_client")
    def test_successful_call_records_duration_and_tokens(self, mock_get_client):
        client = MagicMock()
        client.chat.completions.create.return_value = self._make_response(
            prompt_tokens=42,
            completion_tokens=7,
        )
        mock_get_client.return_value = client

        from workspace.ai.services.llm import call_llm

        before_ok = _sample(
            "ai_request_duration_seconds_count",
            {"model": "gpt-4o-mini", "status": "ok"},
        )
        before_prompt = _sample(
            "ai_tokens_total",
            {"model": "gpt-4o-mini", "kind": "prompt"},
        )
        before_completion = _sample(
            "ai_tokens_total",
            {"model": "gpt-4o-mini", "kind": "completion"},
        )

        call_llm(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(
            _sample(
                "ai_request_duration_seconds_count",
                {"model": "gpt-4o-mini", "status": "ok"},
            )
            - before_ok,
            1,
        )
        self.assertEqual(
            _sample("ai_tokens_total", {"model": "gpt-4o-mini", "kind": "prompt"})
            - before_prompt,
            42,
        )
        self.assertEqual(
            _sample("ai_tokens_total", {"model": "gpt-4o-mini", "kind": "completion"})
            - before_completion,
            7,
        )

    @patch("workspace.ai.client.get_ai_client")
    def test_api_error_observes_duration_with_error_status_and_reraises(
        self, mock_get_client
    ):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("boom")
        mock_get_client.return_value = client

        from workspace.ai.services.llm import call_llm

        before_err = _sample(
            "ai_request_duration_seconds_count",
            {"model": "gpt-4o-mini", "status": "error"},
        )
        with self.assertRaises(RuntimeError):
            call_llm(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(
            _sample(
                "ai_request_duration_seconds_count",
                {"model": "gpt-4o-mini", "status": "error"},
            )
            - before_err,
            1,
        )

    @patch("workspace.ai.client.get_ai_client")
    def test_zero_tokens_does_not_create_a_zero_sample(self, mock_get_client):
        # When usage reports 0 tokens for a kind, we skip the .inc() so the
        # series isn't materialized — keeps the /metrics surface clean.
        client = MagicMock()
        client.chat.completions.create.return_value = self._make_response(
            prompt_tokens=10,
            completion_tokens=0,
        )
        mock_get_client.return_value = client

        from workspace.ai.services.llm import call_llm

        labels_completion = {"model": "gpt-4o-mini", "kind": "completion"}
        before = _sample("ai_tokens_total", labels_completion)
        call_llm(messages=[{"role": "user", "content": "x"}])
        # Counter not bumped for the zero-token kind.
        self.assertEqual(_sample("ai_tokens_total", labels_completion), before)


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
            GenerateImageParams(prompt="a cat"),
            user=None,
            bot=None,
            conversation_id="conv-1",
            context={},
        )

        self.assertEqual(_sample("ai_image_requests_total", labels) - before, 1)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_error_increments_error_counter(self, mock_get_client):
        from workspace.ai.tools import GenerateImageParams, ImageToolProvider

        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("upstream down")
        mock_get_client.return_value = client

        labels = {"model": "dall-e-3", "op": "generate", "status": "error"}
        before = _sample("ai_image_requests_total", labels)

        result = ImageToolProvider().generate_image(
            GenerateImageParams(prompt="a cat"),
            user=None,
            bot=None,
            conversation_id="conv-1",
            context={},
        )

        self.assertTrue(result.startswith("Error"))
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


class ToolLoopMetricsTests(TestCase):
    """Instrumentation of the harness: per-call outcomes, rounds, early stops."""

    def _run(self, script, handler=None, definitions=("search",), **limits):
        toolset = StubToolset(handler=handler, definitions=definitions)
        model = ScriptedModel(script, repeat=True)
        runner = build_runner(
            model,
            toolset,
            observers=[MetricsObserver(toolset, "metrics-model")],
            **limits,
        )
        return runner.run([{"role": "user", "content": "go"}])

    def test_successful_call_counts_as_ok_for_its_tool(self):
        labels = {"tool": "search", "status": "ok"}
        before = _sample("ai_tool_calls_total", labels)

        self._run(
            [tool_reply(call("c1")), reply("done")], handler=lambda tc, ctx: "3 results"
        )

        self.assertEqual(_sample("ai_tool_calls_total", labels) - before, 1)

    def test_error_result_string_counts_as_error(self):
        # Tools report failure to the model as an "Error: ..." string, never
        # as an exception, so that prefix is what the counter reads.
        labels = {"tool": "search", "status": "error"}
        before = _sample("ai_tool_calls_total", labels)

        self._run(
            [tool_reply(call("c1")), reply("done")],
            handler=lambda tc, ctx: "Error: query is required",
        )

        self.assertEqual(_sample("ai_tool_calls_total", labels) - before, 1)

    def test_raising_tool_counts_as_error_and_still_propagates(self):
        def boom(tool_call, context):
            raise RuntimeError("boom")

        labels = {"tool": "search", "status": "error"}
        before = _sample("ai_tool_calls_total", labels)

        with self.assertRaises(RuntimeError):
            self._run([tool_reply(call("c1")), reply("done")], handler=boom)

        self.assertEqual(_sample("ai_tool_calls_total", labels) - before, 1)

    def test_unregistered_tool_name_folds_into_one_series(self):
        invented = {"tool": "teleport_user", "status": "error"}
        folded = {"tool": "unknown", "status": "error"}
        before = _sample("ai_tool_calls_total", folded)

        self._run(
            [tool_reply(call("c1", name="teleport_user")), reply("done")],
            handler=lambda tc, ctx: "Unknown tool: teleport_user",
        )

        self.assertEqual(_sample("ai_tool_calls_total", folded) - before, 1)
        self.assertEqual(_sample("ai_tool_calls_total", invented), 0)

    def test_refused_duplicate_counts_as_repeat_and_stops_the_loop(self):
        repeat = {"tool": "search", "status": "repeat"}
        before_repeat = _sample("ai_tool_calls_total", repeat)
        before_stop = _sample("ai_tool_loop_stops_total", {"reason": "repeat_loop"})

        run = self._run([tool_reply(call("c1"))], handler=lambda tc, ctx: "ok")

        self.assertIs(run.stop, StopReason.REPEAT_LOOP)
        self.assertEqual(_sample("ai_tool_calls_total", repeat) - before_repeat, 1)
        self.assertEqual(
            _sample("ai_tool_loop_stops_total", {"reason": "repeat_loop"})
            - before_stop,
            1,
        )

    def test_round_cap_is_counted_as_an_early_stop(self):
        labels = {"reason": "round_cap"}
        before = _sample("ai_tool_loop_stops_total", labels)

        # Distinct arguments each round, so the repeat guard never fires and
        # the run dies on the round cap instead.
        args = itertools.count()
        run = self._run(
            lambda messages, tools: tool_reply(
                call("c1", arguments=f'{{"q":{next(args)}}}')
            ),
            handler=lambda tc, ctx: "ok",
            max_rounds=2,
        )

        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertEqual(_sample("ai_tool_loop_stops_total", labels) - before, 1)

    def test_rounds_histogram_records_one_sample_per_reply(self):
        labels = {"model": "metrics-model"}
        before_count = _sample("ai_tool_rounds_count", labels)
        before_sum = _sample("ai_tool_rounds_sum", labels)

        self._run(
            [
                tool_reply(call("c1", arguments='{"q":"a"}')),
                tool_reply(call("c2", arguments='{"q":"b"}')),
                reply("done"),
            ],
            handler=lambda tc, ctx: "ok",
        )

        self.assertEqual(_sample("ai_tool_rounds_count", labels) - before_count, 1)
        self.assertEqual(_sample("ai_tool_rounds_sum", labels) - before_sum, 2)

    def test_reply_without_tools_records_a_zero_round_sample(self):
        labels = {"model": "metrics-model"}
        before_count = _sample("ai_tool_rounds_count", labels)
        before_zero = _sample("ai_tool_rounds_bucket", {**labels, "le": "0.0"})

        self._run([reply("hello")])

        self.assertEqual(_sample("ai_tool_rounds_count", labels) - before_count, 1)
        self.assertEqual(
            _sample("ai_tool_rounds_bucket", {**labels, "le": "0.0"}) - before_zero, 1
        )

    def test_cancelled_run_is_left_out_of_the_distribution(self):
        labels = {"model": "metrics-model"}
        before = _sample("ai_tool_rounds_count", labels)

        run = self._run(
            [tool_reply(call("c1"))],
            handler=lambda tc, ctx: "ok",
            is_cancelled=lambda: True,
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(_sample("ai_tool_rounds_count", labels), before)
