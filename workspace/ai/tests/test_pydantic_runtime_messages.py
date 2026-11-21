from django.test import SimpleTestCase
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.messages import ModelResponse as AgentResponse
from pydantic_ai.usage import RequestUsage

from workspace.ai.harness.pydantic_runtime.messages import (
    to_model_messages,
    to_model_response,
)


class ToModelMessagesTests(SimpleTestCase):
    def test_everything_between_two_assistant_turns_is_one_request(self):
        history = to_model_messages(
            [
                {"role": "system", "content": "be kind"},
                {"role": "system", "content": "<context>today</context>"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "and now?"},
            ]
        )

        self.assertEqual(
            [type(message) for message in history],
            [ModelRequest, AgentResponse, ModelRequest],
        )
        self.assertEqual(
            [type(part) for part in history[0].parts],
            [SystemPromptPart, SystemPromptPart, UserPromptPart],
        )
        self.assertEqual(history[0].parts[2].content, "hello")
        self.assertEqual(history[1].parts, [TextPart("hi")])

    def test_a_replayed_tool_round_keeps_its_calls_and_names_its_results(self):
        history = to_model_messages(
            [
                {"role": "user", "content": "search"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": '{"q": 1}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "found"},
                {"role": "assistant", "content": "here"},
            ]
        )

        (asked,) = history[1].parts
        self.assertIsInstance(asked, ToolCallPart)
        self.assertEqual(
            (asked.tool_name, asked.args, asked.tool_call_id),
            ("web_search", '{"q": 1}', "c1"),
        )
        (result,) = history[2].parts
        self.assertIsInstance(result, ToolReturnPart)
        self.assertEqual(
            (result.tool_name, result.content, result.tool_call_id),
            ("web_search", "found", "c1"),
        )

    def test_an_assistant_turn_with_no_text_still_holds_its_place(self):
        history = to_model_messages(
            [
                {"role": "user", "content": "draw"},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "thanks"},
            ]
        )

        self.assertEqual(history[1].parts, [TextPart("")])

    def test_images_of_a_user_message_are_carried_as_images(self):
        (request,) = to_model_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.test/cat.png"},
                        },
                    ],
                }
            ]
        )

        text, inline, linked = request.parts[0].content
        self.assertEqual(text, "what is this?")
        self.assertIsInstance(inline, BinaryContent)
        self.assertEqual(
            (inline.data, inline.media_type), (b"\x00\x00\x00", "image/png")
        )
        self.assertEqual(linked, ImageUrl(url="https://example.test/cat.png"))


class ToModelResponseTests(SimpleTestCase):
    def test_reads_text_reasoning_calls_and_usage(self):
        response = to_model_response(
            AgentResponse(
                parts=[
                    ThinkingPart("let me look"),
                    TextPart("looking"),
                    ToolCallPart("search", '{"q": 1}', tool_call_id="c1"),
                ],
                model_name="gpt-x",
                usage=RequestUsage(input_tokens=12, output_tokens=3),
            ),
            duration=1.5,
        )

        self.assertEqual(response.content, "looking")
        self.assertEqual(response.thinking, "let me look")
        self.assertEqual(
            [(c.id, c.name, c.arguments) for c in response.tool_calls],
            [("c1", "search", '{"q": 1}')],
        )
        self.assertEqual(response.model, "gpt-x")
        self.assertEqual((response.prompt_tokens, response.completion_tokens), (12, 3))
        self.assertEqual(response.duration, 1.5)

    def test_a_backend_reporting_no_usage_leaves_it_unknown(self):
        response = to_model_response(AgentResponse(parts=[TextPart("hi")]))

        self.assertIsNone(response.prompt_tokens)
        self.assertIsNone(response.completion_tokens)

    def test_inline_reasoning_and_leaked_artifacts_are_cut_from_the_text(self):
        response = to_model_response(
            AgentResponse(
                parts=[TextPart("<think>hmm</think>[2026-04-10 20:07] The answer")]
            )
        )

        self.assertEqual(response.content, "The answer")
        self.assertEqual(response.thinking, "hmm")

    def test_reasoning_the_backend_kept_apart_wins_over_inline_tags(self):
        response = to_model_response(
            AgentResponse(
                parts=[ThinkingPart("native"), TextPart("<think>inline</think>Answer")]
            )
        )

        self.assertEqual(response.thinking, "native")
        self.assertEqual(response.content, "Answer")
