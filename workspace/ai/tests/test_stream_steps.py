import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AITask
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
        out, cursor = stream_steps.read_steps(1, None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["data"], {"label": "Search"})
        self.assertEqual(cursor, out[0]["id"])

    def test_reading_does_not_consume_the_mailbox(self):
        stream_steps._enqueue(1, {})
        stream_steps.read_steps(1, None)
        self.assertEqual(len(stream_steps.read_steps(1, None)[0]), 1)

    def test_cursor_returns_only_newer_steps(self):
        stream_steps._enqueue(1, {"label": "a"})
        first = stream_steps.latest_step_id(1)
        stream_steps._enqueue(1, {"label": "b"})
        out, _ = stream_steps.read_steps(1, first)
        self.assertEqual([e["data"]["label"] for e in out], ["b"])

    def test_reading_twice_with_the_returned_cursor_yields_nothing(self):
        stream_steps._enqueue(1, {"label": "a"})
        _, cursor = stream_steps.read_steps(1, None)
        out, again = stream_steps.read_steps(1, cursor)
        self.assertEqual(out, [])
        self.assertEqual(again, cursor)

    def test_evicted_cursor_skips_to_the_tail_instead_of_replaying(self):
        # A cursor the capped window no longer holds must not replay entries
        # the connection already rendered: that shows duplicated step lines.
        stream_steps._enqueue(1, {"label": "a"})
        stream_steps._enqueue(1, {"label": "b"})

        out, cursor = stream_steps.read_steps(1, "evicted")

        self.assertEqual(out, [])
        self.assertEqual(cursor, stream_steps.latest_step_id(1))
        stream_steps._enqueue(1, {"label": "c"})
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.read_steps(1, cursor)[0]], ["c"]
        )

    def test_empty_mailbox_keeps_the_cursor(self):
        self.assertEqual(stream_steps.read_steps(1, "kept"), ([], "kept"))

    def test_latest_step_id_is_none_when_empty(self):
        self.assertIsNone(stream_steps.latest_step_id(1))

    def test_mailbox_is_isolated_per_user(self):
        stream_steps._enqueue(1, {"label": "a"})
        stream_steps._enqueue(2, {"label": "b"})
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.read_steps(1, None)[0]], ["a"]
        )
        self.assertEqual(
            [e["data"]["label"] for e in stream_steps.read_steps(2, None)[0]], ["b"]
        )

    def test_queue_is_capped(self):
        for i in range(stream_steps.MAX_QUEUE + 20):
            stream_steps._enqueue(1, {"i": i})
        out, _ = stream_steps.read_steps(1, None)
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
            out, _ = stream_steps.read_steps(user_id, None)
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
        badge = {
            "icon": "🔍",
            "label": "Web Search",
            "running_label": "Searching the web",
        }
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
        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertIn("🔍", html)
        self.assertIn("Web Search", html)
        self.assertIn("meteo paris", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_step_carries_both_tenses(self, mock_notify):
        # The step is pushed before the tool runs, so the row must read
        # "Looking up profile"; the completion that follows carries no HTML,
        # so the same row has to be able to read "Looked up profile" without
        # being re-rendered. Which one shows is CSS, so both labels ship.
        stream_steps.notify_tool_step(
            [1], "conv-1", make_tool_call(name="get_current_user_info", arguments="{}")
        )

        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertRegex(
            html,
            r'class="ai-step-label-running flex-shrink-0"\s*>\s*Looking up profile',
        )
        self.assertRegex(
            html, r'class="ai-step-label-done flex-shrink-0"\s*>\s*Looked up profile'
        )

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_detail_is_escaped(self, mock_notify):
        # The step HTML is injected client-side via x-html and the detail
        # comes from LLM-generated tool arguments: markup must not survive.
        with patch(
            "workspace.ai.tool_registry.tool_registry.get_detail",
            return_value='<script>alert("x")</script>',
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())
        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_detail_is_truncated(self, mock_notify):
        with patch(
            "workspace.ai.tool_registry.tool_registry.get_detail",
            return_value="x" * 500,
        ):
            stream_steps.notify_tool_step([1], "conv-1", make_tool_call())
        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertIn("x" * stream_steps.MAX_DETAIL_LEN, html)
        self.assertNotIn("x" * (stream_steps.MAX_DETAIL_LEN + 1), html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_invalid_arguments_json_falls_back_to_the_raw_string(self, mock_notify):
        stream_steps.notify_tool_step(
            [1], "conv-1", make_tool_call(arguments="not json{")
        )
        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertIn("search_web", html)
        self.assertIn("not json{", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_row_is_expandable_on_its_arguments_and_has_no_result(self, mock_notify):
        stream_steps.notify_tool_step(
            [1], "conv-1", make_tool_call(arguments='{"query": "meteo paris"}')
        )
        html = stream_steps.read_steps(1, None)[0][0]["data"]["html"]
        self.assertIn("<details", html)
        self.assertIn("query", html)
        self.assertIn("meteo paris", html)
        # The step is pushed before the tool runs, so there is no result block.
        self.assertNotIn("<pre", html)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_step_names_the_call_it_announces(self, mock_notify):
        # The row it opens is ended by call id, not by position: a round's
        # read-only calls run together and finish in any order.
        stream_steps.notify_tool_step([1], "conv-1", make_tool_call())

        step = stream_steps.read_steps(1, None)[0][0]["data"]

        self.assertEqual(step["call_id"], "call_1")

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


class NotifyToolStepDoneTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_enqueues_a_completion_for_each_recipient_and_wakes_them(self, mock_notify):
        stream_steps.notify_tool_step_done([1, 2], "conv-1", make_tool_call())

        for user_id in (1, 2):
            out, _ = stream_steps.read_steps(user_id, None)
            self.assertEqual(len(out), 1)
            step = out[0]["data"]
            self.assertEqual(step["conversation_id"], "conv-1")
            self.assertEqual(step["call_id"], "call_1")
            self.assertIs(step["done"], True)
            # The row is already on screen with both tenses in it, so ending
            # it names it - there is nothing to render again.
            self.assertNotIn("html", step)
        self.assertEqual(mock_notify.call_count, 2)

    @patch("workspace.ai.services.stream_steps.notify_sse")
    def test_a_call_opens_then_closes_one_row(self, mock_notify):
        tool_call = make_tool_call()
        stream_steps.notify_tool_step([1], "conv-1", tool_call)
        stream_steps.notify_tool_step_done([1], "conv-1", tool_call)

        out, _ = stream_steps.read_steps(1, None)

        self.assertEqual([e["data"]["call_id"] for e in out], ["call_1", "call_1"])
        self.assertEqual([e["data"].get("done") for e in out], [None, True])

    def test_no_recipients_is_a_noop(self):
        with patch("workspace.ai.services.stream_steps.notify_sse") as mock_notify:
            stream_steps.notify_tool_step_done([], "conv-1", make_tool_call())
        mock_notify.assert_not_called()

    def test_never_raises_on_broken_notify(self):
        with patch(
            "workspace.ai.services.stream_steps.notify_sse",
            side_effect=RuntimeError("redis down"),
        ):
            stream_steps.notify_tool_step_done([1], "conv-1", make_tool_call())

    def test_completions_reported_from_several_threads_are_all_queued(self):
        """Parallel calls report their end from their own thread.

        The mailbox is a read-modify-write on one cache key, so without the
        enqueue lock two reports racing lose one - and the row it belonged
        to spins until the reply lands.
        """
        calls = [make_tool_call() for _ in range(8)]
        for index, tool_call in enumerate(calls):
            tool_call.id = f"call_{index}"
        start = threading.Barrier(len(calls), timeout=10)

        def report(tool_call):
            start.wait()
            stream_steps.notify_tool_step_done([1], "conv-1", tool_call)

        with patch("workspace.ai.services.stream_steps.notify_sse"):
            with ThreadPoolExecutor(max_workers=len(calls)) as pool:
                list(pool.map(report, calls))

        out, _ = stream_steps.read_steps(1, None)
        self.assertEqual(
            sorted(e["data"]["call_id"] for e in out),
            sorted(tool_call.id for tool_call in calls),
        )


class AIStreamSSEProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="carol", email="c@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot5", email="b5@test.com", password="pw"
        )

    def tearDown(self):
        cache.clear()

    def test_no_running_generation_still_announces_the_empty_snapshot(self):
        # A reconnecting client can be showing a bubble raised before its
        # stream dropped; the empty set is the only thing that lowers it.
        provider = AIStreamSSEProvider(self.user, None)
        self.assertEqual(
            provider.get_initial_events(),
            [("bot_generating", {"conversation_ids": []}, None)],
        )

    def _start_generation(self, conversation, member=True):
        """A conversation with a bot response under way, as the task leaves it."""
        if member:
            ConversationMember.objects.create(conversation=conversation, user=self.user)
        return AITask.objects.create(
            owner=self.bot,
            task_type=AITask.TaskType.CHAT,
            status=AITask.Status.PROCESSING,
            input_data={"conversation_id": str(conversation.pk)},
        )

    def test_a_fresh_connection_learns_which_conversations_are_generating(self):
        # A reload lands with no state: without this the bubble stays down
        # until the next tool, which is a minute away on an image.
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self._start_generation(conversation)

        events = AIStreamSSEProvider(self.user, None).get_initial_events()

        self.assertEqual(events[0][0], "bot_generating")
        self.assertEqual(events[0][1]["conversation_ids"], [str(conversation.pk)])

    def test_generations_in_other_peoples_conversations_are_not_announced(self):
        stranger = User.objects.create_user(
            username="mallory", email="m@test.com", password="pw"
        )
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=stranger
        )
        self._start_generation(conversation, member=False)

        self.assertEqual(
            AIStreamSSEProvider(self.user, None).get_initial_events(),
            [("bot_generating", {"conversation_ids": []}, None)],
        )

    def test_a_finished_generation_is_absent_from_the_snapshot(self):
        # The response landed while the user was away: the connection they
        # reopen on resume must learn the generation is over.
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        task = self._start_generation(conversation)
        task.status = AITask.Status.COMPLETED
        task.save(update_fields=["status"])

        self.assertEqual(
            AIStreamSSEProvider(self.user, None).get_initial_events(),
            [("bot_generating", {"conversation_ids": []}, None)],
        )

    def test_queued_steps_are_replayed_for_a_running_generation(self):
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self._start_generation(conversation)
        stream_steps._enqueue(
            self.user.id,
            {"conversation_id": str(conversation.pk), "html": "<b>Web Search</b>"},
        )

        provider = AIStreamSSEProvider(self.user, None)
        events = provider.get_initial_events()

        self.assertEqual([e[0] for e in events], ["bot_generating", "bot_step"])
        self.assertEqual(events[1][1]["html"], "<b>Web Search</b>")
        # The cursor moved past them: the first poll does not send them twice.
        self.assertEqual(provider.poll(None), [])

    def test_steps_of_a_finished_generation_are_not_replayed(self):
        # The mailbox outlives a generation by its TTL. Replaying those would
        # resurrect a bubble for work that is already done.
        running = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        done = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=done, user=self.user)
        self._start_generation(running)
        stream_steps._enqueue(
            self.user.id, {"conversation_id": str(done.pk), "html": "<b>stale</b>"}
        )

        events = AIStreamSSEProvider(self.user, None).get_initial_events()

        self.assertEqual([e[0] for e in events], ["bot_generating"])

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
