from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.services import stream_steps
from workspace.ai.sse_provider import AIStreamSSEProvider
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


def make_tool_call(name="search_web", arguments='{"query": "meteo paris"}'):
    return SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class MailboxTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_enqueue_then_read_returns_envelope(self):
        stream_steps._enqueue(1, {"label": "Search"})
        out = stream_steps.steps_after(1, None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["data"], {"label": "Search"})
        self.assertIn("id", out[0])

    def test_reading_does_not_consume_the_mailbox(self):
        stream_steps._enqueue(1, {})
        stream_steps.steps_after(1, None)
        self.assertEqual(len(stream_steps.steps_after(1, None)), 1)

    def test_cursor_returns_only_newer_steps(self):
        stream_steps._enqueue(1, {"label": "a"})
        first = stream_steps.latest_step_id(1)
        stream_steps._enqueue(1, {"label": "b"})
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.steps_after(1, first)], ["b"]
        )

    def test_unknown_cursor_resyncs_on_the_whole_window(self):
        stream_steps._enqueue(1, {"label": "a"})
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.steps_after(1, "gone")], ["a"]
        )

    def test_latest_step_id_is_none_when_empty(self):
        self.assertIsNone(stream_steps.latest_step_id(1))

    def test_mailbox_is_isolated_per_user(self):
        stream_steps._enqueue(1, {"label": "a"})
        stream_steps._enqueue(2, {"label": "b"})
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.steps_after(1, None)], ["a"]
        )
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.steps_after(2, None)], ["b"]
        )

    def test_queue_is_capped(self):
        for i in range(stream_steps.MAX_QUEUE + 20):
            stream_steps._enqueue(1, {"i": i})
        out = stream_steps.steps_after(1, None)
        self.assertEqual(len(out), stream_steps.MAX_QUEUE)
        self.assertEqual(out[-1]["data"]["i"], stream_steps.MAX_QUEUE + 19)


class StepRecipientsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(
            username="alice", email="a@test.com", password="pw"
        )
        self.bob = User.objects.create_user(
            username="bob", email="b@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot", email="bot@test.com", password="pw"
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        for user in (self.alice, self.bob, self.bot):
            ConversationMember.objects.create(conversation=self.conversation, user=user)

    def tearDown(self):
        cache.clear()

    def test_returns_active_members_without_the_bot(self):
        recipients = stream_steps.step_recipients(self.conversation.pk, self.bot)
        self.assertEqual(set(recipients), {self.alice.id, self.bob.id})

    def test_excludes_members_who_left(self):
        ConversationMember.objects.filter(
            conversation=self.conversation, user=self.bob
        ).update(left_at=timezone.now())
        recipients = stream_steps.step_recipients(self.conversation.pk, self.bot)
        self.assertEqual(set(recipients), {self.alice.id})


class NotifyToolStepTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_enqueues_for_each_recipient_and_wakes_their_stream(self, mock_notify):
        stream_steps.notify_tool_step([1, 2], "conv-1", make_tool_call())

        for user_id in (1, 2):
            out = stream_steps.steps_after(user_id, None)
            self.assertEqual(len(out), 1)
            step = out[0]["data"]
            self.assertEqual(step["conversation_id"], "conv-1")
            # Unregistered tool -> default badge, no detail
            self.assertIn("⚡", step["html"])
            self.assertIn("search_web", step["html"])
        self.assertEqual(mock_notify.call_count, 2)
        mock_notify.assert_any_call("ai_stream", 1)
        mock_notify.assert_any_call("ai_stream", 2)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_uses_registry_badge_and_detail(self, mock_notify):
        badge = {"icon": "🔍", "label": "Web Search"}
        with (
            patch(
                "workspace.ai.tool_registry.tool_registry.get_badge",
                return_value=badge,
            ),
            patch(
                "workspace.ai.tool_registry.tool_registry.get_detail",
                return_value="meteo paris",
            ),
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())
        html = stream_steps.steps_after(1, None)[0]["data"]["html"]
        self.assertIn("🔍", html)
        self.assertIn("Web Search", html)
        self.assertIn("meteo paris", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_detail_is_escaped(self, mock_notify):
        # The step HTML is injected client-side via x-html and the detail
        # comes from LLM-generated tool arguments: markup must not survive.
        with patch(
            "workspace.ai.tool_registry.tool_registry.get_detail",
            return_value='<script>alert("x")</script>',
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())
        html = stream_steps.steps_after(1, None)[0]["data"]["html"]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_detail_is_truncated(self, mock_notify):
        with patch(
            "workspace.ai.tool_registry.tool_registry.get_detail",
            return_value="x" * 500,
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())
        html = stream_steps.steps_after(1, None)[0]["data"]["html"]
        self.assertIn("x" * stream_steps.MAX_DETAIL_LEN, html)
        self.assertNotIn("x" * (stream_steps.MAX_DETAIL_LEN + 1), html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_invalid_arguments_json_falls_back_to_the_raw_string(self, mock_notify):
        stream_steps.notify_tool_step(
            [1], "conv-1", make_tool_call(arguments="not json{")
        )
        html = stream_steps.steps_after(1, None)[0]["data"]["html"]
        self.assertIn("search_web", html)
        self.assertIn("not json{", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_row_is_expandable_on_its_arguments_and_has_no_result(self, mock_notify):
        stream_steps.notify_tool_step(
            [1], "conv-1", make_tool_call(arguments='{"query": "meteo paris"}')
        )
        html = stream_steps.steps_after(1, None)[0]["data"]["html"]
        self.assertIn("<details", html)
        self.assertIn("query", html)
        self.assertIn("meteo paris", html)
        # The step is pushed before the tool runs, so there is no result block.
        self.assertNotIn("<pre", html)

    def test_no_recipients_is_a_noop(self):
        with patch("workspace.ai.services.stream_steps.notify_sse") as mock_notify:
            stream_steps.notify_tool_step([], "conv-1", make_tool_call())
        mock_notify.assert_not_called()

    def test_never_raises_on_broken_notify(self):
        with patch(
            "workspace.ai.services.stream_steps.notify_sse",
            side_effect=RuntimeError("redis down"),
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())


class AIStreamSSEProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="carol", email="c@test.com", password="pw"
        )

    def tearDown(self):
        cache.clear()

    def test_initial_events_are_empty(self):
        provider = AIStreamSSEProvider(self.user, None)
        self.assertEqual(provider.get_initial_events(), [])

    def test_every_connection_of_a_user_receives_each_step(self):
        """Two tabs open two SSE connections sharing one per-user mailbox.

        A step is a broadcast, not a work item: reading it on one connection
        must not hide it from the others.
        """
        tab_a = AIStreamSSEProvider(self.user, None)
        tab_b = AIStreamSSEProvider(self.user, None)
        stream_steps._enqueue(
            self.user.id, {"conversation_id": "c", "html": "<b>1</b>"}
        )

        events_a = tab_a.poll(None)
        events_b = tab_b.poll(None)

        self.assertEqual([e[1]["html"] for e in events_a], ["<b>1</b>"])
        self.assertEqual([e[1]["html"] for e in events_b], ["<b>1</b>"])

    def test_poll_streams_steps_without_event_ids(self):
        provider = AIStreamSSEProvider(self.user, None)
        stream_steps._enqueue(
            self.user.id,
            {"conversation_id": "conv-1", "html": "<span>Web Search</span>"},
        )

        events = provider.poll(None)

        self.assertEqual(len(events), 1)
        name, data, event_id = events[0]
        self.assertEqual(name, "bot_step")
        self.assertEqual(data["html"], "<span>Web Search</span>")
        self.assertIsNone(event_id)
        # Cursor advanced: the same step is not re-sent.
        self.assertEqual(provider.poll(None), [])

    def test_steps_queued_before_the_connection_are_not_replayed(self):
        stream_steps._enqueue(self.user.id, {"conversation_id": "c", "html": "old"})

        provider = AIStreamSSEProvider(self.user, None)

        self.assertEqual(provider.poll(None), [])
