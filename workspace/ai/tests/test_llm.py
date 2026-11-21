"""The model layer: what reaches a backend, and what comes back from it."""

import json
from unittest.mock import patch

import httpx2
from django.test import SimpleTestCase, override_settings
from pydantic import BaseModel
from pydantic_ai import NativeOutput, direct
from pydantic_ai.exceptions import OutputTokenLimitExceeded, UnexpectedModelBehavior
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from workspace.ai.prompts.base import prompt, send
from workspace.ai.services.llm import (
    get_model,
    messages_for_storage,
    run_prompt,
    usage_fields,
)

from .agents import call, reply, scripted


class _Backend:
    """Answers every HTTP request of the OpenAI client, and keeps what it was sent."""

    def __init__(self, message=None, finish_reason="stop"):
        self.message = message or {"role": "assistant", "content": "hello"}
        self.finish_reason = finish_reason
        self.requests = []

    @property
    def bodies(self):
        return [json.loads(request.content) for request in self.requests]

    def __enter__(self):
        backend = self

        async def answer(transport, request):
            backend.requests.append(request)
            body = json.loads(request.content)
            return httpx2.Response(
                200,
                request=request,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": backend.finish_reason,
                            "message": backend.message,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                        "total_tokens": 7,
                    },
                },
            )

        self._patch = patch.object(
            httpx2.AsyncHTTPTransport, "handle_async_request", answer
        )
        self._patch.start()
        # The one place a test talks to a real model class: its transport is
        # the fake above.
        self._allowed = override_allow_model_requests(True)
        self._allowed.__enter__()
        return self

    def __exit__(self, *exc):
        self._allowed.__exit__(*exc)
        self._patch.stop()


@override_settings(
    AI_API_KEY="test-key", AI_MAX_TOKENS=321, AI_MAX_RETRIES=0, AI_TIMEOUT=30
)
class BackendRequestTests(SimpleTestCase):
    @override_settings(AI_BASE_URL=None, AI_MODEL="gpt-5")
    def test_openai_is_sent_the_reply_cap_under_the_name_it_accepts(self):
        # Its reasoning models, the default one included, refuse max_tokens.
        with _Backend() as backend:
            result = run_prompt(prompt("be brief", "hi"))

        self.assertIsInstance(get_model().provider, OpenAIProvider)
        (request,) = backend.requests
        (body,) = backend.bodies
        self.assertEqual(request.url.host, "api.openai.com")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "gpt-5")
        self.assertEqual(body["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
        )
        self.assertEqual(result.output, "hello")

    @override_settings(AI_BASE_URL="http://ollama.test:11434/v1/", AI_MODEL="gemma")
    def test_another_base_url_is_a_service_that_only_speaks_the_protocol(self):
        with _Backend() as backend:
            run_prompt(prompt("be brief", "hi"), model="other", max_tokens=99)

        self.assertIsInstance(get_model().provider, OpenAICompatibleProvider)
        (request,) = backend.requests
        (body,) = backend.bodies
        self.assertEqual(request.url.host, "ollama.test")
        self.assertEqual(body["model"], "other")
        # Ollama answers uncapped when the cap comes under OpenAI's new name.
        self.assertEqual(body["max_tokens"], 99)
        self.assertNotIn("max_completion_tokens", body)

    @override_settings(AI_BASE_URL="http://ollama.test:11434/v1/", AI_MODEL="gpt-oss")
    def test_reasoning_is_never_sent_back_to_the_model(self):
        # Measured on gpt-oss behind Ollama: given its own reasoning back, it
        # wrote its next tool call into the reasoning five times out of seven.
        thought = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "I should look first.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "look", "arguments": "{}"},
                }
            ],
        }
        with _Backend(thought) as backend:
            history = prompt("be brief", "hi")
            first = direct.model_request_sync(get_model(), history)
            history.append(first)
            send(
                history,
                ToolReturnPart(tool_name="look", content="seen", tool_call_id="c1"),
            )
            direct.model_request_sync(get_model(), history)

        self.assertEqual(first.thinking, "I should look first.")
        self.assertNotIn("I should look first.", json.dumps(backend.bodies[1]))
        (turn,) = [m for m in backend.bodies[1]["messages"] if m["role"] == "assistant"]
        self.assertEqual(turn["tool_calls"][0]["id"], "c1")

    @override_settings(AI_BASE_URL=None, AI_MODEL="gpt-5")
    def test_a_structured_reply_is_asked_for_as_a_strict_schema(self):
        class Payload(BaseModel):
            n: int

        with _Backend({"role": "assistant", "content": '{"n": 3}'}) as backend:
            result = run_prompt(
                prompt("count", "three"), output_type=NativeOutput(Payload, strict=True)
            )

        self.assertEqual(result.output, Payload(n=3))
        schema = backend.bodies[0]["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["schema"]["required"], ["n"])
        self.assertIs(schema["schema"]["additionalProperties"], False)

    @override_settings(AI_API_KEY="")
    def test_no_key_is_no_backend(self):
        with self.assertRaisesMessage(RuntimeError, "AI is not configured"):
            run_prompt(prompt("be brief", "hi"))


class _Payload(BaseModel):
    answer: str
    n: int = 0


class RunPromptTests(SimpleTestCase):
    def test_a_text_reply_comes_back_with_its_cost(self):
        with scripted(reply("Hello", input_tokens=10, output_tokens=2)) as script:
            result = run_prompt(prompt("be brief", "hi"))

        self.assertEqual(result.output, "Hello")
        self.assertEqual(
            usage_fields(result.response, result.usage),
            {"model_used": "test-model", "prompt_tokens": 10, "completion_tokens": 2},
        )
        self.assertEqual(script.tools, [])

    def test_inline_reasoning_and_leaked_artifacts_are_cut_from_the_reply(self):
        with scripted(reply("<thought>hmm</thought>[2026-04-10 20:07] The answer")):
            result = run_prompt(prompt("be brief", "hi"))

        self.assertEqual(result.output, "The answer")
        self.assertEqual(result.response.thinking, "hmm")

    def test_a_structured_reply_is_validated_into_the_schema(self):
        with scripted(reply('{"answer": "yes", "n": 3}')):
            result = run_prompt(prompt("s", "u"), output_type=NativeOutput(_Payload))

        self.assertEqual(result.output, _Payload(answer="yes", n=3))

    def test_json_wrapped_in_a_markdown_fence_is_still_read(self):
        with scripted(reply('```json\n{"answer": "yes"}\n```')):
            result = run_prompt(prompt("s", "u"), output_type=NativeOutput(_Payload))

        self.assertEqual(result.output.answer, "yes")

    def test_a_reply_that_is_not_the_schema_is_asked_again_once_then_fails(self):
        with scripted(reply("not json"), reply('{"n": "x"}')) as script:
            with self.assertRaises(UnexpectedModelBehavior):
                run_prompt(prompt("s", "u"), output_type=NativeOutput(_Payload))

        self.assertEqual(len(script.requests), 2)

    def test_the_second_attempt_can_be_the_right_one(self):
        with scripted(reply("not json"), reply('{"answer": "yes"}')):
            result = run_prompt(prompt("s", "u"), output_type=NativeOutput(_Payload))

        self.assertEqual(result.output.answer, "yes")

    def test_a_reply_cut_at_the_token_cap_says_so(self):
        cut = ModelResponse(parts=[TextPart('{"answer": "ye')], finish_reason="length")
        with scripted(cut):
            with self.assertRaises(OutputTokenLimitExceeded):
                run_prompt(prompt("s", "u"), output_type=NativeOutput(_Payload))


class MessagesForStorageTests(SimpleTestCase):
    def test_a_conversation_is_kept_in_the_librarys_own_shape(self):
        history = [*prompt("be brief", "hi"), reply("looking", call("look", {}, "c1"))]

        request, response = messages_for_storage(history)

        self.assertEqual(request["kind"], "request")
        self.assertEqual(
            [(part["part_kind"], part["content"]) for part in request["parts"]],
            [("system-prompt", "be brief"), ("user-prompt", "hi")],
        )
        self.assertEqual(response["kind"], "response")
        self.assertEqual(response["parts"][1]["tool_name"], "look")

    def test_image_bytes_are_left_out(self):
        image = BinaryContent(data=b"\x89PNG" * 1000, media_type="image/png")

        (request,) = messages_for_storage(prompt("see", ["what is this?", image]))

        text, stored = request["parts"][1]["content"]
        self.assertEqual(text, "what is this?")
        self.assertEqual(stored["data"], "[stripped]")
        self.assertEqual(stored["media_type"], "image/png")

    def test_a_very_long_text_loses_its_tail(self):
        (request,) = messages_for_storage(prompt("s", "x" * 60_000))

        stored = request["parts"][1]["content"]
        self.assertTrue(stored.endswith("… [truncated]"))
        self.assertLess(len(stored), 51_000)
