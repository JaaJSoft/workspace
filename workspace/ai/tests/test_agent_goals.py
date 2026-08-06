import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import AgentGoal, AITask, BotProfile
from workspace.ai.tools import (
    CompleteAgentGoalParams,
    CreateAgentGoalParams,
    UpdateAgentGoalParams,
)
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


def _make_conversation(user, bot_user):
    conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM,
        created_by=user,
    )
    ConversationMember.objects.create(conversation=conversation, user=user)
    ConversationMember.objects.create(conversation=conversation, user=bot_user)
    return conversation


# ---------------------------------------------------------------------------
# 1. Model Tests
# ---------------------------------------------------------------------------


class AgentGoalModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", first_name="AI")
        BotProfile.objects.create(user=self.bot_user, system_prompt="Bot.")
        self.conversation = _make_conversation(self.user, self.bot_user)

    def _goal(self, **kwargs):
        defaults = {
            "conversation": self.conversation,
            "bot": self.bot_user,
            "created_by": self.user,
            "title": "Track apartment listings",
            "goal": "Watch listings in Lyon and report good ones.",
            "next_check_at": timezone.now() + timedelta(hours=1),
        }
        defaults.update(kwargs)
        return AgentGoal.objects.create(**defaults)

    def test_defaults(self):
        goal = self._goal()
        self.assertEqual(goal.status, AgentGoal.Status.ACTIVE)
        self.assertEqual(goal.check_count, 0)
        self.assertEqual(goal.notes, "")
        self.assertEqual(goal.outcome, "")
        self.assertIsNone(goal.last_checked_at)
        self.assertIsNone(goal.deadline)

    def test_str(self):
        goal = self._goal()
        s = str(goal)
        self.assertIn("active", s)
        self.assertIn("Track apartment listings", s)

    def test_clamp_next_check_raises_past_to_floor(self):
        past = timezone.now() - timedelta(hours=2)
        clamped = AgentGoal.clamp_next_check(past)
        self.assertGreater(clamped, timezone.now())
        self.assertLessEqual(
            clamped,
            timezone.now() + AgentGoal.MIN_CHECK_INTERVAL + timedelta(seconds=5),
        )

    def test_clamp_next_check_keeps_future_value(self):
        future = timezone.now() + timedelta(days=3)
        self.assertEqual(AgentGoal.clamp_next_check(future), future)


# ---------------------------------------------------------------------------
# 2. Dispatcher Tests
# ---------------------------------------------------------------------------


class DispatchAgentGoalsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        BotProfile.objects.create(user=self.bot_user, system_prompt="Bot.")
        self.conversation = _make_conversation(self.user, self.bot_user)

    def _goal(self, **kwargs):
        defaults = {
            "conversation": self.conversation,
            "bot": self.bot_user,
            "created_by": self.user,
            "title": "Goal",
            "goal": "Objective.",
            "next_check_at": timezone.now() - timedelta(minutes=5),
        }
        defaults.update(kwargs)
        return AgentGoal.objects.create(**defaults)

    @patch("workspace.ai.tasks.agent_goals.run_agent_goal_check.delay")
    def test_dispatches_due_goals(self, mock_delay):
        goal = self._goal()

        from workspace.ai.tasks.agent_goals import dispatch_agent_goals

        dispatch_agent_goals()

        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.args[0], str(goal.uuid))

    @patch("workspace.ai.tasks.agent_goals.run_agent_goal_check.delay")
    def test_skips_future_goals(self, mock_delay):
        self._goal(next_check_at=timezone.now() + timedelta(hours=1))

        from workspace.ai.tasks.agent_goals import dispatch_agent_goals

        dispatch_agent_goals()

        mock_delay.assert_not_called()

    @patch("workspace.ai.tasks.agent_goals.run_agent_goal_check.delay")
    def test_skips_non_active_goals(self, mock_delay):
        for goal_status in (
            AgentGoal.Status.PAUSED,
            AgentGoal.Status.COMPLETED,
            AgentGoal.Status.ABANDONED,
        ):
            self._goal(status=goal_status)

        from workspace.ai.tasks.agent_goals import dispatch_agent_goals

        dispatch_agent_goals()

        mock_delay.assert_not_called()

    @patch("workspace.ai.tasks.agent_goals.run_agent_goal_check.delay")
    def test_does_not_double_enqueue_on_back_to_back_runs(self, mock_delay):
        goal = self._goal()

        from workspace.ai.tasks.agent_goals import dispatch_agent_goals

        dispatch_agent_goals()
        dispatch_agent_goals()

        mock_delay.assert_called_once()
        goal.refresh_from_db()
        self.assertGreater(goal.next_check_at, timezone.now())


# ---------------------------------------------------------------------------
# 3. Check-in Worker Tests
# ---------------------------------------------------------------------------


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
    AI_CHAT_CONTEXT_SIZE=50,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class RunAgentGoalCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(
            username="bot", first_name="AI", last_name="Bot"
        )
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user, system_prompt="You are a test bot."
        )
        self.conversation = _make_conversation(self.user, self.bot_user)

    def _goal(self, **kwargs):
        defaults = {
            "conversation": self.conversation,
            "bot": self.bot_user,
            "created_by": self.user,
            "title": "Research topic",
            "goal": "Research a topic over time.",
            "next_check_at": timezone.now() - timedelta(minutes=1),
        }
        defaults.update(kwargs)
        return AgentGoal.objects.create(**defaults)

    @staticmethod
    def _llm_result(content):
        return {
            "content": content,
            "tool_calls": None,
            "message": MagicMock(content=content, tool_calls=None, to_dict=lambda: {}),
            "model": "gpt-4o-mini",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_posts_message_and_advances_goal(self, mock_llm):
        mock_llm.return_value = self._llm_result("Found something interesting!")
        goal = self._goal()

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(goal.uuid))

        self.assertEqual(result["status"], "ok")
        bot_msg = Message.objects.filter(
            conversation=self.conversation, author=self.bot_user
        ).first()
        self.assertIsNotNone(bot_msg)
        self.assertEqual(bot_msg.body, "Found something interesting!")

        goal.refresh_from_db()
        self.assertEqual(goal.check_count, 1)
        self.assertIsNotNone(goal.last_checked_at)
        # Fallback next check-in: the agent didn't set one, so ~24h from now.
        self.assertGreater(goal.next_check_at, timezone.now() + timedelta(hours=23))

        task = AITask.objects.get(owner=self.bot_user)
        self.assertEqual(task.task_type, AITask.TaskType.AGENT)
        self.assertEqual(task.status, AITask.Status.COMPLETED)

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_silent_checkin_posts_no_message(self, mock_llm):
        mock_llm.return_value = self._llm_result("[SILENT]")
        goal = self._goal()

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(goal.uuid))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "silent")
        self.assertFalse(
            Message.objects.filter(
                conversation=self.conversation, author=self.bot_user
            ).exists()
        )
        task = AITask.objects.get(owner=self.bot_user)
        self.assertEqual(task.result, "[SILENT]")
        # The goal still advanced: silence is a completed check-in.
        goal.refresh_from_db()
        self.assertEqual(goal.check_count, 1)

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_skips_non_active_goal(self, mock_llm):
        goal = self._goal(status=AgentGoal.Status.PAUSED)

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(goal.uuid))

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_active")
        mock_llm.assert_not_called()

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_goal_not_found(self, mock_llm):
        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(uuid.uuid4()))

        self.assertEqual(result["status"], "error")
        mock_llm.assert_not_called()

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_duplicate_delivery_skipped_by_cas(self, mock_llm):
        mock_llm.return_value = self._llm_result("Hello")
        goal = self._goal()
        stale_token = (timezone.now() - timedelta(hours=2)).isoformat()

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(goal.uuid), claim_token=stale_token)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_claimed")
        mock_llm.assert_not_called()

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_empty_response_posts_nothing(self, mock_llm):
        mock_llm.return_value = self._llm_result("")
        goal = self._goal()

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        result = run_agent_goal_check(str(goal.uuid))

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "empty_response")
        self.assertFalse(
            Message.objects.filter(
                conversation=self.conversation, author=self.bot_user
            ).exists()
        )

    @patch("workspace.ai.services.tool_loop.call_llm")
    def test_goal_instruction_injected_in_system_prompt(self, mock_llm):
        mock_llm.return_value = self._llm_result("[SILENT]")
        goal = self._goal(notes="Previous findings: nothing yet.")

        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        run_agent_goal_check(str(goal.uuid))

        messages = mock_llm.call_args.args[0]
        system_content = messages[0]["content"]
        self.assertIn("Autonomous goal check-in", system_content)
        self.assertIn(str(goal.uuid), system_content)
        self.assertIn("Research a topic over time.", system_content)
        self.assertIn("Previous findings: nothing yet.", system_content)


# ---------------------------------------------------------------------------
# 4. API Tests
# ---------------------------------------------------------------------------


class AgentGoalAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(
            username="bot", first_name="AI", last_name="Bot"
        )
        BotProfile.objects.create(user=self.bot_user, system_prompt="Bot.")
        self.conversation = _make_conversation(self.user, self.bot_user)

        self.goal = AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            title="Coach marathon prep",
            goal="Coach the user until the October marathon.",
            next_check_at=timezone.now() + timedelta(days=1),
        )

    def _list_url(self):
        return f"/api/v1/chat/conversations/{self.conversation.uuid}/goals"

    def _detail_url(self, goal_id=None):
        gid = goal_id or self.goal.uuid
        return f"/api/v1/chat/conversations/{self.conversation.uuid}/goals/{gid}"

    def test_list_goals(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["title"], "Coach marathon prep")
        self.assertEqual(resp.data[0]["status"], "active")

    def test_list_goals_unauthenticated(self):
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_goals_non_member(self):
        other = User.objects.create_user(username="other", password="pass123")
        self.client.force_authenticate(other)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_closed_goals_hidden_from_list(self):
        self.goal.status = AgentGoal.Status.COMPLETED
        self.goal.save(update_fields=["status"])
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 0)

    def test_update_goal_text(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            self._detail_url(),
            data={"goal": "Coach the user until the November marathon."},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.goal.refresh_from_db()
        self.assertIn("November", self.goal.goal)

    def test_pause_and_resume_goal(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            self._detail_url(), data={"status": "paused"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.PAUSED)

        resp = self.client.patch(
            self._detail_url(), data={"status": "active"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.ACTIVE)

    def test_cannot_set_closed_status_via_patch(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            self._detail_url(), data={"status": "completed"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_notes_are_read_only(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            self._detail_url(), data={"notes": "injected"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.notes, "")

    def test_delete_marks_abandoned(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._detail_url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.ABANDONED)
        self.assertEqual(self.goal.outcome, "Stopped by the user.")

    def test_delete_nonexistent_goal(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._detail_url(goal_id=uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# 5. Tool Tests
# ---------------------------------------------------------------------------


class AgentGoalToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(
            username="bot", first_name="AI", last_name="Bot"
        )
        BotProfile.objects.create(user=self.bot_user, system_prompt="Bot.")
        self.conversation = _make_conversation(self.user, self.bot_user)

        from workspace.ai.tools import AgentGoalToolProvider

        self.provider = AgentGoalToolProvider()
        self.conv_id = str(self.conversation.uuid)
        self.context = {}

    def _call(self, method_name, args=None):
        method = getattr(self.provider, method_name)
        return method(
            args,
            user=self.user,
            bot=self.bot_user,
            conversation_id=self.conv_id,
            context=self.context,
        )

    def _goal(self, **kwargs):
        defaults = {
            "conversation": self.conversation,
            "bot": self.bot_user,
            "created_by": self.user,
            "title": "Existing goal",
            "goal": "Do something over time.",
            "next_check_at": timezone.now() + timedelta(hours=6),
        }
        defaults.update(kwargs)
        return AgentGoal.objects.create(**defaults)

    # -- create --------------------------------------------------------------

    def test_create_goal(self):
        first_check = (timezone.now() + timedelta(hours=3)).isoformat()
        result = self._call(
            "create_agent_goal",
            CreateAgentGoalParams(
                title="Track news",
                goal="Track AI news and report weekly.",
                first_check_at=first_check,
            ),
        )
        self.assertIn("Created goal", result)
        goal = AgentGoal.objects.get(conversation=self.conversation)
        self.assertEqual(goal.title, "Track news")
        self.assertEqual(goal.status, AgentGoal.Status.ACTIVE)
        self.assertEqual(goal.created_by, self.user)

    def test_create_goal_invalid_datetime(self):
        result = self._call(
            "create_agent_goal",
            CreateAgentGoalParams(
                title="Bad", goal="Bad datetime.", first_check_at="not-a-date"
            ),
        )
        self.assertIn("Error", result)
        self.assertFalse(AgentGoal.objects.exists())

    def test_create_goal_clamps_too_soon_check(self):
        first_check = (timezone.now() + timedelta(seconds=30)).isoformat()
        result = self._call(
            "create_agent_goal",
            CreateAgentGoalParams(
                title="Eager", goal="Checks too often.", first_check_at=first_check
            ),
        )
        self.assertIn("Created goal", result)
        goal = AgentGoal.objects.get(conversation=self.conversation)
        self.assertGreaterEqual(
            goal.next_check_at,
            timezone.now() + AgentGoal.MIN_CHECK_INTERVAL - timedelta(seconds=5),
        )

    def test_create_goal_respects_active_limit(self):
        for i in range(AgentGoal.MAX_ACTIVE_PER_CONVERSATION):
            self._goal(title=f"Goal {i}")
        result = self._call(
            "create_agent_goal",
            CreateAgentGoalParams(
                title="One too many",
                goal="Overflow.",
                first_check_at=(timezone.now() + timedelta(hours=1)).isoformat(),
            ),
        )
        self.assertIn("Error", result)
        self.assertEqual(
            AgentGoal.objects.count(), AgentGoal.MAX_ACTIVE_PER_CONVERSATION
        )

    def test_create_goal_with_deadline(self):
        result = self._call(
            "create_agent_goal",
            CreateAgentGoalParams(
                title="Deadline goal",
                goal="Finish before the deadline.",
                first_check_at=(timezone.now() + timedelta(hours=1)).isoformat(),
                deadline=(timezone.now() + timedelta(days=30)).isoformat(),
            ),
        )
        self.assertIn("Created goal", result)
        goal = AgentGoal.objects.get(conversation=self.conversation)
        self.assertIsNotNone(goal.deadline)

    # -- list ----------------------------------------------------------------

    def test_list_goals_empty(self):
        result = self._call("list_agent_goals", {})
        self.assertIn("No active goals", result)

    def test_list_goals(self):
        self._goal(notes="Working notes here.")
        result = self._call("list_agent_goals", {})
        self.assertIn("Existing goal", result)
        self.assertIn("Working notes here.", result)

    # -- update --------------------------------------------------------------

    def test_update_notes_and_next_check(self):
        goal = self._goal()
        next_check = (timezone.now() + timedelta(days=2)).isoformat()
        result = self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(
                goal_id=goal.uuid,
                notes="New findings recorded.",
                next_check_at=next_check,
            ),
        )
        self.assertIn("Updated goal", result)
        goal.refresh_from_db()
        self.assertEqual(goal.notes, "New findings recorded.")
        self.assertGreater(goal.next_check_at, timezone.now() + timedelta(days=1))

    def test_update_unknown_goal(self):
        result = self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(goal_id=uuid.uuid4(), notes="Nope."),
        )
        self.assertIn("Error", result)

    def test_update_pause(self):
        goal = self._goal()
        result = self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(goal_id=goal.uuid, status="paused"),
        )
        self.assertIn("Updated goal", result)
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.PAUSED)

    def test_update_rejects_closed_status(self):
        goal = self._goal()
        result = self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(goal_id=goal.uuid, status="completed"),
        )
        self.assertIn("Error", result)
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.ACTIVE)

    def test_update_nothing_provided(self):
        goal = self._goal()
        result = self._call(
            "update_agent_goal", UpdateAgentGoalParams(goal_id=goal.uuid)
        )
        self.assertIn("Error", result)

    def test_update_clamps_next_check(self):
        goal = self._goal()
        result = self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(
                goal_id=goal.uuid,
                next_check_at=(timezone.now() - timedelta(hours=1)).isoformat(),
            ),
        )
        self.assertIn("Updated goal", result)
        goal.refresh_from_db()
        self.assertGreater(goal.next_check_at, timezone.now())

    # -- complete ------------------------------------------------------------

    def test_complete_goal(self):
        goal = self._goal()
        result = self._call(
            "complete_agent_goal",
            CompleteAgentGoalParams(goal_id=goal.uuid, outcome="Mission accomplished."),
        )
        self.assertIn("completed", result)
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.COMPLETED)
        self.assertEqual(goal.outcome, "Mission accomplished.")

    def test_abandon_goal(self):
        goal = self._goal()
        result = self._call(
            "complete_agent_goal",
            CompleteAgentGoalParams(
                goal_id=goal.uuid, outcome="No longer relevant.", abandoned=True
            ),
        )
        self.assertIn("abandoned", result)
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.ABANDONED)

    def test_complete_unknown_goal(self):
        result = self._call(
            "complete_agent_goal",
            CompleteAgentGoalParams(goal_id=uuid.uuid4(), outcome="Ghost."),
        )
        self.assertIn("Error", result)


# ---------------------------------------------------------------------------
# 6. Prompt Tests
# ---------------------------------------------------------------------------


class AgentGoalPromptTests(TestCase):
    def test_system_prompt_includes_autonomous_goals_section(self):
        from workspace.ai.prompts.chat import build_chat_messages

        messages = build_chat_messages("You are a bot.", [])
        self.assertIn("## Autonomous goals", messages[0]["content"])
        self.assertIn("create_agent_goal", messages[0]["content"])
