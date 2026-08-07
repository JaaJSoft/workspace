import json
from importlib import import_module
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from workspace.ai.tool_registry import tool_registry
from workspace.chat.models import Conversation, Message
from workspace.chat.ui.templatetags.chat_tags import render_ai_steps

migration = import_module(
    "workspace.chat.migrations.0023_strip_tool_badges_from_body_html"
)


class FakeMessage:
    def __init__(self, tool_data):
        self.tool_data = tool_data


def make_round(
    name="some_tool",
    arguments='{"query": "hello"}',
    call_id="call_1",
    result_content='"ok"',
):
    return {
        "assistant_content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
        "results": [{"tool_call_id": call_id, "content": result_content}],
    }


def make_thinking_round(thinking="deep thought"):
    return {"thinking": thinking, "tool_calls": [], "results": []}


def tool_steps(ctx):
    return [s for s in ctx["steps"] if s["type"] == "tool"]


class RenderAiStepsTagTests(SimpleTestCase):
    def test_non_list_tool_data_yields_no_steps(self):
        for tool_data in (None, {}, {"type": "call", "state": "active"}, "junk"):
            ctx = render_ai_steps(FakeMessage(tool_data))
            self.assertEqual(ctx["steps"], [], tool_data)

    def test_unknown_tool_falls_back_to_generic_badge(self):
        ctx = render_ai_steps(FakeMessage([make_round(name="nope_tool")]))
        call = tool_steps(ctx)[0]
        self.assertEqual(call["icon"], "⚡")
        self.assertEqual(call["label"], "nope_tool")
        self.assertEqual(call["detail"], "")

    def test_registry_badge_and_detail_are_used(self):
        with (
            patch.object(
                tool_registry,
                "get_badge",
                return_value={"icon": "🔍", "label": "Searched the web"},
            ),
            patch.object(tool_registry, "get_detail", return_value="hello"),
        ):
            ctx = render_ai_steps(FakeMessage([make_round()]))
        call = tool_steps(ctx)[0]
        self.assertEqual(call["icon"], "🔍")
        self.assertEqual(call["label"], "Searched the web")
        self.assertEqual(call["detail"], "hello")
        self.assertEqual(call["args"], [("query", "hello")])
        self.assertEqual(call["args_raw"], "")

    def test_invalid_arguments_json_kept_raw(self):
        ctx = render_ai_steps(FakeMessage([make_round(arguments="{not json")]))
        call = tool_steps(ctx)[0]
        self.assertEqual(call["args"], [])
        self.assertEqual(call["args_raw"], "{not json")
        self.assertEqual(call["detail"], "")

    def test_non_string_arg_values_are_json_encoded(self):
        ctx = render_ai_steps(
            FakeMessage([make_round(arguments='{"count": 3, "tags": ["a", "b"]}')])
        )
        self.assertEqual(
            tool_steps(ctx)[0]["args"],
            [("count", "3"), ("tags", '["a", "b"]')],
        )

    def test_missing_result_shows_empty_not_error(self):
        rnd = make_round()
        rnd["results"] = []
        ctx = render_ai_steps(FakeMessage([rnd]))
        call = tool_steps(ctx)[0]
        self.assertEqual(call["result"], "")
        self.assertFalse(call["is_error"])

    def test_json_result_is_pretty_printed(self):
        payload = json.dumps([{"title": "A", "url": "http://x"}])
        ctx = render_ai_steps(FakeMessage([make_round(result_content=payload)]))
        self.assertEqual(
            tool_steps(ctx)[0]["result"],
            json.dumps(
                [{"title": "A", "url": "http://x"}], indent=2, ensure_ascii=False
            ),
        )

    def test_plain_text_result_kept_verbatim(self):
        text = "Line one\nLine two … [truncated]"
        ctx = render_ai_steps(FakeMessage([make_round(result_content=text)]))
        self.assertEqual(tool_steps(ctx)[0]["result"], text)

    def test_error_results_are_flagged(self):
        for content in ("Error: boom", "Unknown tool: nope"):
            ctx = render_ai_steps(FakeMessage([make_round(result_content=content)]))
            call = tool_steps(ctx)[0]
            self.assertTrue(call["is_error"], content)
            self.assertEqual(call["result"], content)
        # Legitimate result starting with "Error" without colon should not be flagged
        ctx = render_ai_steps(
            FakeMessage([make_round(result_content="Error handling guide")])
        )
        call = tool_steps(ctx)[0]
        self.assertFalse(call["is_error"])
        self.assertEqual(call["result"], "Error handling guide")

    def test_non_string_result_content_is_treated_as_empty(self):
        # Non-string result content (dict) should not crash and should render as empty
        rnd = make_round()
        rnd["results"] = [{"tool_call_id": "call_1", "content": {"nested": True}}]
        ctx = render_ai_steps(FakeMessage([rnd]))
        call = tool_steps(ctx)[0]
        self.assertEqual(call["result"], "")
        self.assertFalse(call["is_error"])

    def test_multiple_rounds_flatten_in_order(self):
        rounds = [
            make_round(name="tool_a", call_id="call_a"),
            make_round(name="tool_b", call_id="call_b"),
        ]
        ctx = render_ai_steps(FakeMessage(rounds))
        self.assertEqual([c["label"] for c in tool_steps(ctx)], ["tool_a", "tool_b"])

    def test_malformed_rounds_are_skipped(self):
        rounds = ["junk", {"tool_calls": ["junk"], "results": "junk"}, make_round()]
        ctx = render_ai_steps(FakeMessage(rounds))
        self.assertEqual(len(ctx["steps"]), 1)


class ReasoningStepsTests(SimpleTestCase):
    def test_round_produces_thinking_text_then_tools_in_order(self):
        rnd = make_round()
        rnd["thinking"] = "let me check"
        rnd["assistant_content"] = "Checking now..."
        ctx = render_ai_steps(FakeMessage([rnd]))
        self.assertEqual(
            [s["type"] for s in ctx["steps"]], ["thinking", "text", "tool"]
        )
        self.assertEqual(ctx["steps"][0]["text"], "let me check")
        self.assertEqual(ctx["steps"][1]["text"], "Checking now...")

    def test_final_thinking_only_round(self):
        ctx = render_ai_steps(FakeMessage([make_round(), make_thinking_round()]))
        self.assertEqual([s["type"] for s in ctx["steps"]], ["tool", "thinking"])
        self.assertEqual(ctx["tool_count"], 1)
        self.assertTrue(ctx["has_reasoning"])
        self.assertTrue(ctx["collapsed"])

    def test_legacy_rounds_without_thinking_stay_tools_only(self):
        ctx = render_ai_steps(FakeMessage([make_round()]))
        self.assertEqual([s["type"] for s in ctx["steps"]], ["tool"])
        self.assertFalse(ctx["has_reasoning"])
        self.assertFalse(ctx["collapsed"])

    def test_more_than_three_tool_steps_collapse_without_reasoning(self):
        rounds = [make_round(call_id=f"c{i}") for i in range(4)]
        ctx = render_ai_steps(FakeMessage(rounds))
        self.assertFalse(ctx["has_reasoning"])
        self.assertTrue(ctx["collapsed"])

    def test_blank_or_non_string_thinking_ignored(self):
        for bad in ("", "   ", None, {"x": 1}):
            rnd = make_round()
            rnd["thinking"] = bad
            ctx = render_ai_steps(FakeMessage([rnd]))
            self.assertEqual([s["type"] for s in ctx["steps"]], ["tool"], bad)


def render_partial(tool_data):
    return render_to_string(
        "chat/ui/partials/_ai_steps.html",
        render_ai_steps(FakeMessage(tool_data)),
    )


class AiStepsPartialTests(SimpleTestCase):
    def test_empty_steps_render_nothing(self):
        self.assertEqual(render_partial(None).strip(), "")

    def test_three_or_fewer_tools_without_reasoning_are_inline(self):
        rounds = [make_round(name=f"tool_{i}", call_id=f"call_{i}") for i in range(3)]
        html = render_partial(rounds)
        self.assertNotIn("Used 3 tools", html)
        self.assertEqual(html.count("<details"), 3)

    def test_more_than_three_tools_collapse_behind_summary(self):
        rounds = [make_round(name=f"tool_{i}", call_id=f"call_{i}") for i in range(4)]
        html = render_partial(rounds)
        self.assertIn("Used 4 tools", html)
        # 1 outer wrapper + 4 per-call rows
        self.assertEqual(html.count("<details"), 5)

    def test_reasoning_collapses_behind_reasoned_summary(self):
        rnd = make_round()
        rnd["thinking"] = "let me look"
        html = render_partial([rnd, make_thinking_round("final thought")])
        self.assertIn("Reasoned", html)
        self.assertIn("used 1 tool", html)
        # Outer wrapper + 1 tool row + 2 thinking rows, all <details>
        self.assertEqual(html.count("<details"), 4)
        self.assertIn("let me look", html)
        self.assertIn("final thought", html)

    def test_intermediate_text_step_is_rendered(self):
        rnd = make_round()
        rnd["assistant_content"] = "Searching the web now"
        html = render_partial([rnd])
        self.assertIn("Searching the web now", html)
        self.assertIn("Reasoned", html)

    def test_thinking_is_escaped(self):
        rnd = make_round()
        rnd["thinking"] = "<script>alert(1)</script>"
        html = render_partial([rnd])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_detail_and_result_are_escaped(self):
        rnd = make_round(
            arguments=json.dumps({"query": "<script>alert(1)</script>"}),
            result_content="<img src=x onerror=alert(1)>",
        )
        with patch.object(
            tool_registry, "get_detail", return_value="<script>alert(1)</script>"
        ):
            html = render_partial([rnd])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<img src=x", html)

    def test_error_result_gets_error_styling(self):
        html = render_partial([make_round(result_content="Error: boom")])
        self.assertIn("text-error", html)

    def test_finished_timeline_renders_the_past_tense_alone(self):
        # Every call in a stored message has already run: the present-tense
        # span belongs to the live steps only.
        html = render_partial([make_round()])
        self.assertIn("ai-step-label-done", html)
        self.assertNotIn("ai-step-label-running", html)

    def test_args_and_result_shown_in_expanded_content(self):
        html = render_partial([make_round()])
        self.assertIn("query", html)
        self.assertIn("hello", html)
        self.assertIn("ok", html)


BADGE_BLOCK = (
    '\n<div class="mt-2 text-xs text-base-content/40 flex items-center gap-1 flex-wrap">'
    "<span>🔍</span> Searched the web: hello</div>"
)


class StripToolBadgesMigrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="alice", email="a@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )

    def _message(self, body_html, tool_data):
        return Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body="hi",
            body_html=body_html,
            tool_data=tool_data,
        )

    def test_badge_block_stripped_when_tool_data_is_list(self):
        msg = self._message("<p>hi</p>" + BADGE_BLOCK, [make_round()])
        migration.strip_tool_badges(django_apps, None)
        msg.refresh_from_db()
        self.assertEqual(msg.body_html, "<p>hi</p>")

    def test_multiline_badge_variant_stripped(self):
        block = (
            '\n<div class="mt-2 text-xs text-base-content/40 flex flex-col gap-0.5">'
            '<div class="flex items-center gap-1"><span>🎨</span> Generated image: x</div>'
            "</div>"
        )
        msg = self._message("<p>hi</p>" + block, [make_round()])
        migration.strip_tool_badges(django_apps, None)
        msg.refresh_from_db()
        self.assertEqual(msg.body_html, "<p>hi</p>")

    def test_dict_tool_data_untouched(self):
        html = "<p>call</p>" + BADGE_BLOCK
        msg = self._message(html, {"type": "call", "state": "ended"})
        migration.strip_tool_badges(django_apps, None)
        msg.refresh_from_db()
        self.assertEqual(msg.body_html, html)

    def test_null_tool_data_untouched(self):
        html = "<p>old</p>" + BADGE_BLOCK
        msg = self._message(html, None)
        migration.strip_tool_badges(django_apps, None)
        msg.refresh_from_db()
        self.assertEqual(msg.body_html, html)

    def test_no_marker_untouched(self):
        msg = self._message("<p>plain</p>", [make_round()])
        migration.strip_tool_badges(django_apps, None)
        msg.refresh_from_db()
        self.assertEqual(msg.body_html, "<p>plain</p>")
