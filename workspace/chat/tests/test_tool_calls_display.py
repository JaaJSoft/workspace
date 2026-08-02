import json
from importlib import import_module
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from workspace.ai.tool_registry import tool_registry
from workspace.chat.models import Conversation, Message
from workspace.chat.ui.templatetags.chat_tags import render_tool_calls

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


class RenderToolCallsTagTests(SimpleTestCase):
    def test_non_list_tool_data_yields_no_calls(self):
        for tool_data in (None, {}, {"type": "call", "state": "active"}, "junk"):
            ctx = render_tool_calls(FakeMessage(tool_data))
            self.assertEqual(ctx["calls"], [], tool_data)

    def test_unknown_tool_falls_back_to_generic_badge(self):
        ctx = render_tool_calls(FakeMessage([make_round(name="nope_tool")]))
        call = ctx["calls"][0]
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
            ctx = render_tool_calls(FakeMessage([make_round()]))
        call = ctx["calls"][0]
        self.assertEqual(call["icon"], "🔍")
        self.assertEqual(call["label"], "Searched the web")
        self.assertEqual(call["detail"], "hello")
        self.assertEqual(call["args"], [("query", "hello")])
        self.assertEqual(call["args_raw"], "")

    def test_invalid_arguments_json_kept_raw(self):
        ctx = render_tool_calls(FakeMessage([make_round(arguments="{not json")]))
        call = ctx["calls"][0]
        self.assertEqual(call["args"], [])
        self.assertEqual(call["args_raw"], "{not json")
        self.assertEqual(call["detail"], "")

    def test_non_string_arg_values_are_json_encoded(self):
        ctx = render_tool_calls(
            FakeMessage([make_round(arguments='{"count": 3, "tags": ["a", "b"]}')])
        )
        self.assertEqual(
            ctx["calls"][0]["args"],
            [("count", "3"), ("tags", '["a", "b"]')],
        )

    def test_missing_result_shows_empty_not_error(self):
        rnd = make_round()
        rnd["results"] = []
        ctx = render_tool_calls(FakeMessage([rnd]))
        call = ctx["calls"][0]
        self.assertEqual(call["result"], "")
        self.assertFalse(call["is_error"])

    def test_json_result_is_pretty_printed(self):
        payload = json.dumps([{"title": "A", "url": "http://x"}])
        ctx = render_tool_calls(FakeMessage([make_round(result_content=payload)]))
        self.assertEqual(
            ctx["calls"][0]["result"],
            json.dumps(
                [{"title": "A", "url": "http://x"}], indent=2, ensure_ascii=False
            ),
        )

    def test_plain_text_result_kept_verbatim(self):
        text = "Line one\nLine two … [truncated]"
        ctx = render_tool_calls(FakeMessage([make_round(result_content=text)]))
        self.assertEqual(ctx["calls"][0]["result"], text)

    def test_error_results_are_flagged(self):
        for content in ("Error: boom", "Unknown tool: nope"):
            ctx = render_tool_calls(FakeMessage([make_round(result_content=content)]))
            call = ctx["calls"][0]
            self.assertTrue(call["is_error"], content)
            self.assertEqual(call["result"], content)

    def test_multiple_rounds_flatten_in_order(self):
        rounds = [
            make_round(name="tool_a", call_id="call_a"),
            make_round(name="tool_b", call_id="call_b"),
        ]
        ctx = render_tool_calls(FakeMessage(rounds))
        self.assertEqual([c["label"] for c in ctx["calls"]], ["tool_a", "tool_b"])

    def test_malformed_rounds_are_skipped(self):
        rounds = ["junk", {"tool_calls": ["junk"], "results": "junk"}, make_round()]
        ctx = render_tool_calls(FakeMessage(rounds))
        self.assertEqual(len(ctx["calls"]), 1)


def render_partial(tool_data):
    return render_to_string(
        "chat/ui/partials/_tool_calls.html",
        render_tool_calls(FakeMessage(tool_data)),
    )


class ToolCallsPartialTests(SimpleTestCase):
    def test_empty_calls_render_nothing(self):
        self.assertEqual(render_partial(None).strip(), "")

    def test_three_or_fewer_rows_are_directly_visible(self):
        rounds = [make_round(name=f"tool_{i}", call_id=f"call_{i}") for i in range(3)]
        html = render_to_string(
            "chat/ui/partials/_tool_calls.html",
            render_tool_calls(FakeMessage(rounds)),
        )
        self.assertNotIn("Used 3 tools", html)
        self.assertEqual(html.count("<details"), 3)

    def test_more_than_three_rows_collapse_behind_summary(self):
        rounds = [make_round(name=f"tool_{i}", call_id=f"call_{i}") for i in range(4)]
        html = render_partial(rounds)
        self.assertIn("Used 4 tools", html)
        # 1 outer wrapper + 4 per-call rows
        self.assertEqual(html.count("<details"), 5)

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
