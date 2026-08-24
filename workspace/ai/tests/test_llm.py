import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from pydantic import BaseModel

from workspace.ai.services.llm import (
    _extract_thinking,
    build_tool_content,
    call_llm,
    call_llm_structured,
    extract_text_tool_calls,
    serialize_response,
    truncate_middle,
    truncate_tool_result,
)


class ExtractTextToolCallsTests(TestCase):
    def test_returns_none_when_no_json(self):
        calls, remaining = extract_text_tool_calls("just a regular reply")
        self.assertIsNone(calls)
        self.assertEqual(remaining, "just a regular reply")

    def test_returns_none_when_json_is_not_a_tool_call(self):
        content = 'reply with {"unrelated": "data"}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertIsNone(calls)
        self.assertEqual(remaining, content)

    def test_shorthand_tool_form(self):
        content = '{"tool": "search_files", "query": "report", "limit": 5}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, "search_files")
        # Remaining keys are re-emitted as the arguments JSON.
        self.assertEqual(json.loads(args_json), {"query": "report", "limit": 5})
        self.assertEqual(remaining, "")

    def test_openai_like_form_with_dict_arguments(self):
        content = (
            '{"name": "create_event", "arguments": {"title": "Lunch", "duration": 30}}'
        )
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, "create_event")
        self.assertEqual(json.loads(args_json), {"title": "Lunch", "duration": 30})
        self.assertEqual(remaining, "")

    def test_openai_like_form_with_string_arguments(self):
        content = '{"name": "echo", "arguments": "raw string"}'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, "echo")
        # String arguments must be passed through untouched (not double-encoded).
        self.assertEqual(args_json, "raw string")

    def test_text_around_tool_call_is_returned_in_remaining(self):
        content = 'Here you go: {"tool": "noop"} - that is all'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "noop")
        self.assertIn("Here you go:", remaining)
        self.assertIn("that is all", remaining)
        self.assertNotIn('{"tool"', remaining)

    def test_multiple_tool_calls_in_one_message(self):
        content = '{"tool": "a", "x": 1} and also {"name": "b", "arguments": {"y": 2}}'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "a")
        self.assertEqual(json.loads(calls[0][1]), {"x": 1})
        self.assertEqual(calls[1][0], "b")
        self.assertEqual(json.loads(calls[1][1]), {"y": 2})

    def test_invalid_json_is_ignored(self):
        content = '{not really json} but {"tool": "ok"} should still work'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ok")

    def test_non_dict_json_is_ignored(self):
        content = '{"tool": "ok"}'
        # Plain list/string JSON would also be matched by the regex; ensure only dicts are taken.
        # Adding a list before the dict should not break parsing of the dict.
        calls, _ = extract_text_tool_calls("[1, 2, 3] " + content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ok")

    def test_raw_tool_call_tags_are_stripped(self):
        # <tool_call> wrapper tags some models emit must not block parsing.
        content = '<tool_call>{"tool": "search", "q": "x"}</tool_call>'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "search")

    def test_shorthand_with_no_extra_keys(self):
        # When the shorthand form has only the "tool" key, the arguments JSON is an empty object.
        content = '{"tool": "ping"}'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ping")
        self.assertEqual(json.loads(calls[0][1]), {})


class ExtractThinkingTests(SimpleTestCase):
    def test_no_think_block_returns_empty_thinking(self):
        thinking, cleaned = _extract_thinking("just a reply")
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, "just a reply")

    def test_think_block_is_captured_and_stripped(self):
        thinking, cleaned = _extract_thinking(
            "<think>plan the answer</think>Hello there"
        )
        self.assertEqual(thinking, "plan the answer")
        self.assertEqual(cleaned, "Hello there")

    def test_multiple_blocks_join_with_blank_line(self):
        thinking, cleaned = _extract_thinking(
            "<think>first</think>mid<think>second</think>end"
        )
        self.assertEqual(thinking, "first\n\nsecond")
        self.assertEqual(cleaned, "midend")

    def test_unclosed_tag_captures_nothing_and_keeps_content(self):
        content = "<think>never closed... Hello"
        thinking, cleaned = _extract_thinking(content)
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, content)

    def test_case_insensitive(self):
        thinking, cleaned = _extract_thinking("<THINK>loud</THINK>hi")
        self.assertEqual(thinking, "loud")
        self.assertEqual(cleaned, "hi")

    def test_tag_spelling_variants_are_captured(self):
        for tag in ("thought", "thoughts", "thinking", "reasoning"):
            with self.subTest(tag=tag):
                thinking, cleaned = _extract_thinking(
                    f"<{tag}>weighing options</{tag}>Hello there"
                )
                self.assertEqual(thinking, "weighing options")
                self.assertEqual(cleaned, "Hello there")

    def test_mismatched_open_and_close_tags_are_left_alone(self):
        content = "<think>oops</thought>Hello"
        thinking, cleaned = _extract_thinking(content)
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, content)

    def test_mismatched_block_does_not_swallow_the_next_one(self):
        thinking, cleaned = _extract_thinking(
            "<think>bad</thought><think>real</think>Answer"
        )
        self.assertEqual(thinking, "real")
        self.assertEqual(cleaned, "<think>bad</thought>Answer")


def _fake_client(message):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model="test-model",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
    )
    completions = SimpleNamespace(create=lambda **kwargs: response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


class CallLlmThinkingTests(SimpleTestCase):
    def _call(self, message):
        with patch(
            "workspace.ai.client.get_ai_client",
            return_value=_fake_client(message),
        ):
            return call_llm([{"role": "user", "content": "hi"}], model="m")

    def test_think_tags_populate_thinking(self):
        msg = SimpleNamespace(content="<think>let me see</think>Hello", tool_calls=None)
        result = self._call(msg)
        self.assertEqual(result["thinking"], "let me see")
        self.assertEqual(result["content"], "Hello")

    def test_native_reasoning_content_wins_over_tags(self):
        msg = SimpleNamespace(
            content="<think>tag</think>Hello",
            tool_calls=None,
            reasoning_content="native reasoning",
        )
        result = self._call(msg)
        self.assertEqual(result["thinking"], "native reasoning")
        self.assertEqual(result["content"], "Hello")

    def test_openrouter_reasoning_field_is_read(self):
        msg = SimpleNamespace(
            content="Hello", tool_calls=None, reasoning="or reasoning"
        )
        result = self._call(msg)
        self.assertEqual(result["thinking"], "or reasoning")

    def test_no_thinking_yields_empty_string(self):
        msg = SimpleNamespace(content="Hello", tool_calls=None)
        result = self._call(msg)
        self.assertEqual(result["thinking"], "")

    def test_blank_reasoning_content_falls_back_to_reasoning(self):
        msg = SimpleNamespace(
            content="Hello",
            tool_calls=None,
            reasoning_content="   ",
            reasoning="or reasoning",
        )
        result = self._call(msg)
        self.assertEqual(result["thinking"], "or reasoning")

    def test_non_string_native_reasoning_is_ignored(self):
        msg = SimpleNamespace(
            content="Hello", tool_calls=None, reasoning_content={"odd": True}
        )
        result = self._call(msg)
        self.assertEqual(result["thinking"], "")


class _Payload(BaseModel):
    items: list[str]


class CallLlmStructuredTests(SimpleTestCase):
    def _call(self, content):
        msg = SimpleNamespace(content=content, tool_calls=None)
        with patch(
            "workspace.ai.client.get_ai_client",
            return_value=_fake_client(msg),
        ):
            return call_llm_structured(
                [{"role": "user", "content": "hi"}], _Payload, model="m"
            )

    def test_valid_json_returns_validated_instance(self):
        parsed, result = self._call('{"items": ["a", "b"]}')
        self.assertEqual(parsed.items, ["a", "b"])
        self.assertEqual(result["model"], "test-model")

    def test_fenced_json_is_tolerated(self):
        parsed, _ = self._call('```json\n{"items": ["a"]}\n```')
        self.assertEqual(parsed.items, ["a"])

    def test_invalid_json_returns_none_with_usage(self):
        parsed, result = self._call("not json")
        self.assertIsNone(parsed)
        self.assertEqual(result["prompt_tokens"], 1)
        self.assertEqual(result["completion_tokens"], 2)

    def test_schema_mismatch_returns_none(self):
        parsed, _ = self._call('{"items": "nope"}')
        self.assertIsNone(parsed)

    def test_think_tags_are_stripped_before_parsing(self):
        parsed, _ = self._call('<think>hm</think>{"items": []}')
        self.assertEqual(parsed.items, [])

    def test_sends_strict_json_schema_response_format(self):
        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": '{"items": []}'},
        ) as mock_call:
            call_llm_structured([{"role": "user", "content": "hi"}], _Payload)
        response_format = mock_call.call_args.kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["name"], "_Payload")
        self.assertIs(response_format["json_schema"]["strict"], True)
        schema = response_format["json_schema"]["schema"]
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["items"])

    def test_strict_schema_normalizes_nested_models(self):
        class Item(BaseModel):
            name: str
            note: str = ""
            when: str | None = None

        class Envelope(BaseModel):
            entries: list[Item]

        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": '{"entries": []}'},
        ) as mock_call:
            call_llm_structured([{"role": "user", "content": "hi"}], Envelope)
        schema = mock_call.call_args.kwargs["response_format"]["json_schema"]["schema"]
        item = schema["$defs"]["Item"]
        self.assertIs(item["additionalProperties"], False)
        self.assertEqual(item["required"], ["name", "note", "when"])
        self.assertNotIn("default", item["properties"]["note"])
        self.assertNotIn("default", item["properties"]["when"])

    def test_strict_schema_spares_a_property_named_default(self):
        class Odd(BaseModel):
            default: str = "x"
            items: list[str] = []

        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": '{"default": "a", "items": []}'},
        ) as mock_call:
            call_llm_structured([{"role": "user", "content": "hi"}], Odd)
        schema = mock_call.call_args.kwargs["response_format"]["json_schema"]["schema"]
        self.assertIn("default", schema["properties"])
        self.assertIn("items", schema["properties"])
        self.assertEqual(schema["required"], ["default", "items"])


class SerializeResponseThinkingTests(SimpleTestCase):
    def test_thinking_included(self):
        result = {"content": "hi", "thinking": "why not", "model": "m"}
        self.assertEqual(serialize_response(result)["thinking"], "why not")

    def test_missing_thinking_defaults_empty(self):
        self.assertEqual(serialize_response({"content": "hi"})["thinking"], "")


class ImagePayloadTextTests(TestCase):
    def test_build_tool_content_uses_payload_text(self):
        payload = json.dumps(
            {
                "type": "image",
                "mime_type": "image/png",
                "data": "QUJD",
                "text": "Image generated successfully for: a cat",
            }
        )
        content = build_tool_content(payload)
        self.assertEqual(
            content[0],
            {"type": "text", "text": "Image generated successfully for: a cat"},
        )
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,QUJD")

    def test_build_tool_content_falls_back_without_text(self):
        payload = json.dumps(
            {"type": "image", "mime_type": "image/webp", "data": "QUJD"}
        )
        content = build_tool_content(payload)
        self.assertEqual(content[0], {"type": "text", "text": "Here is the image:"})

    def test_truncate_tool_result_keeps_text(self):
        payload = json.dumps(
            {"type": "image", "data": "QUJD", "text": "Image edited: darker"}
        )
        self.assertEqual(
            json.loads(truncate_tool_result(payload, 2000)),
            {"type": "image", "data": "[stripped]", "text": "Image edited: darker"},
        )


class TruncateMiddleTests(TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(truncate_middle("hello", 100), "hello")

    def test_head_and_tail_survive_the_cut(self):
        text = "START" + ("x" * 5000) + "CONCLUSION"
        out = truncate_middle(text, 500)
        self.assertLessEqual(len(out), 500)
        self.assertTrue(out.startswith("START"))
        self.assertTrue(out.endswith("CONCLUSION"))
        self.assertIn("characters omitted from the middle", out)

    def test_hint_names_the_call_in_the_residue(self):
        out = truncate_middle(
            "y" * 5000, 500, hint="fetch_url(https://example.com/doc)"
        )
        self.assertIn("of fetch_url(https://example.com/doc)", out)
        self.assertLessEqual(len(out), 500)

    def test_budget_too_small_for_a_marker_falls_back_to_a_hard_cut(self):
        out = truncate_middle("z" * 500, 20)
        self.assertEqual(out, "z" * 20)

    def test_tool_result_is_cut_in_the_middle_not_at_the_tail(self):
        text = "HEAD" + ("m" * 9000) + "TAIL"
        out = truncate_tool_result(text, 1000, hint="fetch_url(https://x.test)")
        self.assertLessEqual(len(out), 1000)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))
        self.assertIn("fetch_url(https://x.test)", out)
