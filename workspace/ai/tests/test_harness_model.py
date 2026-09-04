from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from workspace.ai.harness.model import LLMModel, ModelResponse, ToolCall


def _sdk_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _result(content, *, message_content=None, tool_calls=None, thinking=""):
    return {
        "content": content,
        "thinking": thinking,
        "tool_calls": tool_calls,
        "message": SimpleNamespace(
            role="assistant",
            content=content if message_content is None else message_content,
            tool_calls=tool_calls,
        ),
        "model": "m",
        "prompt_tokens": 3,
        "completion_tokens": 4,
    }


class ModelResponseTests(SimpleTestCase):
    def test_reads_a_call_llm_result(self):
        response = ModelResponse.from_call_llm(
            _result("hi", tool_calls=[_sdk_call("c1", "search", '{"q": 1}')])
        )

        self.assertEqual(response.content, "hi")
        self.assertEqual(response.tool_calls, [ToolCall("c1", "search", '{"q": 1}')])
        self.assertEqual(response.model, "m")
        self.assertEqual((response.prompt_tokens, response.completion_tokens), (3, 4))

    def test_raw_content_is_what_the_backend_wrote(self):
        # The cleaned text is for readers; the assistant turn echoes the
        # backend's own text, reasoning tags included, as it always has.
        response = ModelResponse.from_call_llm(
            _result("hi", message_content="<think>why</think>hi")
        )

        self.assertEqual(response.content, "hi")
        self.assertEqual(response.raw_content, "<think>why</think>hi")
        self.assertEqual(
            response.as_assistant_message(),
            {"role": "assistant", "content": "<think>why</think>hi"},
        )

    def test_a_result_without_a_message_falls_back_to_its_content(self):
        response = ModelResponse.from_call_llm({"content": "plain"})

        self.assertEqual(response.raw_content, "plain")
        self.assertEqual(response.tool_calls, [])

    def test_assistant_message_carries_the_calls_in_wire_shape(self):
        response = ModelResponse(
            raw_content="", tool_calls=[ToolCall("c1", "search", "{}")]
        )

        self.assertEqual(
            response.as_assistant_message(),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            },
        )

    def test_record_matches_the_stored_shape(self):
        response = ModelResponse(
            content="hi",
            thinking="why",
            tool_calls=[ToolCall("c1", "search", "{}")],
            model="m",
            prompt_tokens=1,
            completion_tokens=2,
        )

        self.assertEqual(
            response.as_record(),
            {
                "content": "hi",
                "thinking": "why",
                "tool_calls": [{"id": "c1", "name": "search", "arguments": "{}"}],
                "model": "m",
                "prompt_tokens": 1,
                "completion_tokens": 2,
            },
        )
        self.assertIsNone(ModelResponse(content="hi").as_record()["tool_calls"])


class LLMModelTests(SimpleTestCase):
    @patch("workspace.ai.harness.model.call_llm")
    def test_passes_the_request_through(self, mock_call_llm):
        mock_call_llm.return_value = _result("hi")
        tools = [{"type": "function", "function": {"name": "search"}}]

        response = LLMModel("m").complete(
            [{"role": "user", "content": "go"}], tools=tools
        )

        mock_call_llm.assert_called_once_with(
            [{"role": "user", "content": "go"}], model="m", tools=tools
        )
        self.assertEqual(response.content, "hi")

    @patch("workspace.ai.harness.model.call_llm")
    def test_calls_written_as_text_are_read_when_tools_were_offered(
        self, mock_call_llm
    ):
        mock_call_llm.return_value = _result(
            'Let me look. {"name": "search", "arguments": {"q": "x"}}'
        )

        response = LLMModel("m").complete([], tools=[])

        (tool_call,) = response.tool_calls
        self.assertEqual(tool_call.name, "search")
        self.assertEqual(tool_call.arguments, '{"q": "x"}')
        self.assertTrue(tool_call.id.startswith("call_"))
        # The text the call was cut from is what remains, on both faces.
        self.assertEqual(response.content, "Let me look.")
        self.assertEqual(response.raw_content, "Let me look.")

    @patch("workspace.ai.harness.model.call_llm")
    def test_a_tool_less_request_is_never_read_for_calls(self, mock_call_llm):
        # The run asked for a final answer: JSON in it is the answer.
        mock_call_llm.return_value = _result('{"name": "search", "arguments": {}}')

        response = LLMModel("m").complete([])

        self.assertEqual(response.tool_calls, [])
        self.assertEqual(response.content, '{"name": "search", "arguments": {}}')

    @patch("workspace.ai.harness.model.call_llm")
    def test_native_calls_are_not_second_guessed(self, mock_call_llm):
        mock_call_llm.return_value = _result(
            '{"name": "other", "arguments": {}}',
            tool_calls=[_sdk_call("c1", "search", "{}")],
        )

        response = LLMModel("m").complete([], tools=[])

        self.assertEqual([tc.name for tc in response.tool_calls], ["search"])
