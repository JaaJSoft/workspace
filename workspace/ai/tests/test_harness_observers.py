from unittest.mock import call as mock_call
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.ai.harness.observers import StreamStepsObserver
from workspace.chat.models import Conversation, ConversationMember

from .harness import ScriptedModel, StubToolset, build_runner, call, reply, tool_reply

User = get_user_model()


class StepEmissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="erin", email="e@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot4", email="b4@test.com", password="pw"
        )
        self.leaver = User.objects.create_user(
            username="frank", email="f@test.com", password="pw"
        )

    def _run(self, model, toolset, conversation_id):
        runner = build_runner(
            model,
            toolset,
            user=self.user,
            bot=self.bot,
            conversation_id=conversation_id,
            observers=[StreamStepsObserver(conversation_id, self.bot)],
        )
        return runner.run([{"role": "user", "content": "go"}])

    @patch("workspace.ai.harness.observers.notify_tool_step_done")
    @patch("workspace.ai.harness.observers.notify_tool_step")
    @patch("workspace.ai.harness.observers.step_recipients", return_value=[1, 2])
    def test_each_tool_execution_emits_a_step(
        self, mock_recipients, mock_notify, mock_done
    ):
        tool_call = call("call_1")
        model = ScriptedModel([tool_reply(tool_call), reply("Done")])

        self._run(model, StubToolset(handler=lambda tc, ctx: "ok"), "conv-1")

        # Membership is re-read for the completion too: a member who leaves
        # while a tool runs must not receive its end either.
        self.assertEqual(
            mock_recipients.call_args_list, [mock_call("conv-1", self.bot)] * 2
        )
        mock_notify.assert_called_once_with([1, 2], "conv-1", tool_call)
        mock_done.assert_called_once_with([1, 2], "conv-1", tool_call)

    @patch("workspace.ai.harness.observers.notify_tool_step_done")
    @patch("workspace.ai.harness.observers.notify_tool_step")
    @patch("workspace.ai.harness.observers.step_recipients")
    def test_no_conversation_id_skips_recipient_lookup(
        self, mock_recipients, mock_notify, mock_done
    ):
        model = ScriptedModel([tool_reply(call("call_1")), reply("Done")])

        self._run(model, StubToolset(handler=lambda tc, ctx: "ok"), None)

        mock_recipients.assert_not_called()
        mock_notify.assert_not_called()
        mock_done.assert_not_called()

    @patch("workspace.ai.harness.observers.notify_tool_step")
    def test_member_leaving_mid_run_stops_receiving_steps(self, mock_notify):
        """Recipients are re-read per tool, not snapshotted for the whole run."""
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        for member in (self.user, self.leaver, self.bot):
            ConversationMember.objects.create(conversation=conversation, user=member)

        def leave_after_first_tool(tool_call, context):
            ConversationMember.objects.filter(
                conversation=conversation, user=self.leaver
            ).update(left_at=timezone.now())
            return "ok"

        model = ScriptedModel(
            [tool_reply(call("call_1")), tool_reply(call("call_2")), reply("Done")]
        )

        self._run(model, StubToolset(handler=leave_after_first_tool), conversation.pk)

        delivered = [set(c.args[0]) for c in mock_notify.call_args_list]
        self.assertEqual(delivered, [{self.user.id, self.leaver.id}, {self.user.id}])


class StepCompletionTests(TestCase):
    """Each call reports its own end, so the UI never guesses from position."""

    def setUp(self):
        self.bot = User.objects.create_user(
            username="step-bot", email="sb@test.com", password="pw"
        )

    def _run(self, toolset, calls, conversation_id="conv-1"):
        events = toolset.spans
        model = ScriptedModel([tool_reply(*calls), reply("done")])
        with (
            patch("workspace.ai.harness.observers.step_recipients", return_value=[7]),
            patch(
                "workspace.ai.harness.observers.notify_tool_step",
                side_effect=lambda ids, conv, tc: events.append((tc.id, "step")),
            ),
            patch(
                "workspace.ai.harness.observers.notify_tool_step_done",
                side_effect=lambda ids, conv, tc: events.append((tc.id, "done")),
            ),
        ):
            build_runner(
                model,
                toolset,
                bot=self.bot,
                conversation_id=conversation_id,
                observers=[StreamStepsObserver(conversation_id, self.bot)],
            ).run([{"role": "user", "content": "go"}])
        return events

    def test_a_call_reports_its_end_once_it_has_returned(self):
        events = self._run(
            StubToolset(),
            [call("c1", name="get_weather", arguments='{"location": "Lyon"}')],
        )

        self.assertEqual(
            events, [("c1", "step"), ("c1", "start"), ("c1", "end"), ("c1", "done")]
        )

    def test_the_call_that_ends_first_reports_first(self):
        # The whole point of the completion event: c2 was dispatched second
        # and finished first, and its row has to stop spinning right then -
        # not when c1, the slow one it shares a batch with, catches up.
        import threading

        finished_second = threading.Event()

        def handler(tool_call, context):
            if tool_call.id == "c1":
                finished_second.wait(10)
                return "slow"
            finished_second.set()
            return "fast"

        toolset = StubToolset(concurrent={"read_webpage"}, handler=handler)
        calls = [
            call("c1", name="read_webpage", arguments='{"url": "https://a.test"}'),
            call("c2", name="read_webpage", arguments='{"url": "https://b.test"}'),
        ]

        events = self._run(toolset, calls)

        # Both rows are announced in call order before either runs.
        self.assertEqual(events[:2], [("c1", "step"), ("c2", "step")])
        self.assertLess(events.index(("c2", "done")), events.index(("c1", "end")))

    def test_a_refused_repeat_announces_nothing_and_ends_nothing(self):
        repeated = '{"location": "Lyon"}'
        calls = [
            call(f"c{i}", name="get_weather", arguments=repeated) for i in range(1, 5)
        ]

        events = self._run(StubToolset(), calls)

        # The fourth identical call is refused before it could run, so its
        # row never appears and never has to be ended.
        self.assertEqual([call_id for call_id, _ in events if call_id == "c4"], [])
        self.assertEqual(
            events[-4:],
            [("c3", "step"), ("c3", "start"), ("c3", "end"), ("c3", "done")],
        )

    def test_a_run_outside_a_conversation_notifies_nobody(self):
        with (
            patch("workspace.ai.harness.observers.notify_tool_step") as mock_step,
            patch("workspace.ai.harness.observers.notify_tool_step_done") as mock_done,
        ):
            self._run(
                StubToolset(), [call("c1", name="get_weather")], conversation_id=None
            )

        mock_step.assert_not_called()
        mock_done.assert_not_called()
