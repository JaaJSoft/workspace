import uuid
from datetime import time, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile, ScheduledMessage
from workspace.ai.tools import CancelScheduleParams, ScheduleMessageParams
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


# ---------------------------------------------------------------------------
# 1. Model Tests
# ---------------------------------------------------------------------------

class ScheduledMessageModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(
            username='bot', first_name='AI', last_name='Bot',
        )
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are a test bot.',
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)

    def test_once_schedule_deactivates_after_compute(self):
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Say hello',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() + timedelta(hours=1),
        )
        self.assertTrue(schedule.is_active)
        schedule.compute_next_run()
        self.assertFalse(schedule.is_active)

    def test_recurring_hours_computes_next_run(self):
        now = timezone.now()
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Check in',
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.HOURS,
            recurrence_interval=3,
            next_run_at=now,
        )
        schedule.last_run_at = now
        schedule.compute_next_run()

        expected = now + timedelta(hours=3)
        # Allow 1 second tolerance
        self.assertAlmostEqual(
            schedule.next_run_at.timestamp(),
            expected.timestamp(),
            delta=1,
        )
        self.assertTrue(schedule.is_active)

    def test_recurring_days_with_time(self):
        now = timezone.now()
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Good morning',
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.DAYS,
            recurrence_interval=1,
            recurrence_time=time(9, 0),
            next_run_at=now,
        )
        schedule.last_run_at = now
        schedule.compute_next_run()

        self.assertEqual(schedule.next_run_at.hour, 9)
        self.assertEqual(schedule.next_run_at.minute, 0)
        self.assertTrue(schedule.is_active)

    def test_str(self):
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Test',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() + timedelta(hours=1),
        )
        s = str(schedule)
        self.assertIn('once', s)
        self.assertIn(str(self.conversation.uuid), s)


# ---------------------------------------------------------------------------
# 2. Dispatcher Tests
# ---------------------------------------------------------------------------

class DispatchScheduledMessagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(username='bot', password='pass123')
        BotProfile.objects.create(user=self.bot_user, system_prompt='Bot.')
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)

    @patch('workspace.ai.tasks.generate_scheduled_response.delay')
    def test_dispatches_due_schedules(self, mock_delay):
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Due now',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )

        from workspace.ai.tasks import dispatch_scheduled_messages
        dispatch_scheduled_messages()

        mock_delay.assert_called_once_with(str(schedule.uuid))

    @patch('workspace.ai.tasks.generate_scheduled_response.delay')
    def test_skips_future_schedules(self, mock_delay):
        ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Not yet',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() + timedelta(hours=1),
        )

        from workspace.ai.tasks import dispatch_scheduled_messages
        dispatch_scheduled_messages()

        mock_delay.assert_not_called()

    @patch('workspace.ai.tasks.generate_scheduled_response.delay')
    def test_skips_inactive_schedules(self, mock_delay):
        ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Inactive',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() - timedelta(minutes=5),
            is_active=False,
        )

        from workspace.ai.tasks import dispatch_scheduled_messages
        dispatch_scheduled_messages()

        mock_delay.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Generate Scheduled Response Tests
# ---------------------------------------------------------------------------

@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
    AI_CHAT_CONTEXT_SIZE=50,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GenerateScheduledResponseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(
            username='bot', first_name='AI', last_name='Bot',
        )
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are a test bot.',
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)

    @patch('workspace.ai.tasks._call_llm')
    def test_generates_message_and_deactivates_once(self, mock_llm):
        mock_llm.return_value = {
            'content': 'Hello!',
            'tool_calls': None,
            'message': MagicMock(content='Hello!', tool_calls=None, to_dict=lambda: {}),
            'model': 'gpt-4o-mini',
            'prompt_tokens': 10,
            'completion_tokens': 5,
        }

        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Say hello to the user',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

        from workspace.ai.tasks import generate_scheduled_response
        result = generate_scheduled_response(str(schedule.uuid))

        self.assertEqual(result['status'], 'ok')

        # Bot message was created
        bot_msg = Message.objects.filter(
            conversation=self.conversation,
            author=self.bot_user,
        ).first()
        self.assertIsNotNone(bot_msg)
        self.assertEqual(bot_msg.body, 'Hello!')

        # Schedule was deactivated
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertIsNotNone(schedule.last_run_at)

    @patch('workspace.ai.tasks._call_llm')
    def test_recurring_stays_active(self, mock_llm):
        mock_llm.return_value = {
            'content': 'Check-in time!',
            'tool_calls': None,
            'message': MagicMock(content='Check-in time!', tool_calls=None, to_dict=lambda: {}),
            'model': 'gpt-4o-mini',
            'prompt_tokens': 10,
            'completion_tokens': 5,
        }

        now = timezone.now()
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Check in with the user',
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.HOURS,
            recurrence_interval=2,
            next_run_at=now - timedelta(minutes=1),
        )

        from workspace.ai.tasks import generate_scheduled_response
        result = generate_scheduled_response(str(schedule.uuid))

        self.assertEqual(result['status'], 'ok')

        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)
        self.assertIsNotNone(schedule.last_run_at)
        # next_run_at should have advanced (at least 1 hour in the future from last_run_at)
        self.assertGreater(schedule.next_run_at, schedule.last_run_at)

    @patch('workspace.ai.tasks._call_llm')
    def test_skips_inactive_schedule(self, mock_llm):
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Should not run',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() - timedelta(minutes=1),
            is_active=False,
        )

        from workspace.ai.tasks import generate_scheduled_response
        result = generate_scheduled_response(str(schedule.uuid))

        self.assertEqual(result['status'], 'skipped')
        mock_llm.assert_not_called()

    @patch('workspace.ai.tasks._call_llm')
    def test_empty_response_skips_message(self, mock_llm):
        """Scheduled messages with empty AI responses should not post empty messages."""
        mock_llm.return_value = {
            'content': '',
            'tool_calls': None,
            'message': MagicMock(content='', tool_calls=None, to_dict=lambda: {}),
            'model': 'gpt-4o-mini',
            'prompt_tokens': 10,
            'completion_tokens': 0,
        }

        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Say hello',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

        from workspace.ai.tasks import generate_scheduled_response
        result = generate_scheduled_response(str(schedule.uuid))

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'empty_response')

        # No bot message should have been created
        bot_msg = Message.objects.filter(
            conversation=self.conversation,
            author=self.bot_user,
        ).first()
        self.assertIsNone(bot_msg)

        # OpenAI should have been called twice (initial + retry)
        self.assertEqual(mock_llm.call_count, 2)

    def test_nonexistent_schedule(self):
        from workspace.ai.tasks import generate_scheduled_response
        result = generate_scheduled_response(str(uuid.uuid4()))
        self.assertEqual(result['status'], 'error')
        self.assertIn('not found', result['error'])


# ---------------------------------------------------------------------------
# 4. API Tests
# ---------------------------------------------------------------------------

class ScheduledMessageAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(
            username='bot', first_name='AI', last_name='Bot',
        )
        BotProfile.objects.create(user=self.bot_user, system_prompt='Bot.')
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)

        self.schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Daily greeting',
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit='days',
            recurrence_interval=1,
            recurrence_time=time(9, 0),
            next_run_at=timezone.now() + timedelta(days=1),
        )

    def _list_url(self):
        return f'/api/v1/chat/conversations/{self.conversation.uuid}/schedules'

    def _detail_url(self, schedule_id=None):
        sid = schedule_id or self.schedule.uuid
        return f'/api/v1/chat/conversations/{self.conversation.uuid}/schedules/{sid}'

    def test_list_schedules(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['prompt'], 'Daily greeting')

    def test_list_schedules_unauthenticated(self):
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_schedules_non_member(self):
        other = User.objects.create_user(username='other', password='pass123')
        self.client.force_authenticate(other)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_schedule_prompt(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            self._detail_url(),
            data={'prompt': 'Updated greeting'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.prompt, 'Updated greeting')

    def test_delete_schedule(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._detail_url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)

    def test_delete_nonexistent_schedule(self):
        self.client.force_authenticate(self.user)
        fake_id = uuid.uuid4()
        resp = self.client.delete(self._detail_url(schedule_id=fake_id))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_inactive_schedules_hidden_from_list(self):
        self.schedule.is_active = False
        self.schedule.save(update_fields=['is_active'])
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 0)


# ---------------------------------------------------------------------------
# 5. Tool Tests
# ---------------------------------------------------------------------------

class ScheduleToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(
            username='bot', first_name='AI', last_name='Bot',
        )
        BotProfile.objects.create(user=self.bot_user, system_prompt='Bot.')
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)

        from workspace.ai.tools import ScheduleToolProvider
        self.provider = ScheduleToolProvider()
        self.conv_id = str(self.conversation.uuid)
        self.context = {}

    def _call(self, method_name, args):
        method = getattr(self.provider, method_name)
        return method(
            args,
            user=self.user,
            bot=self.bot_user,
            conversation_id=self.conv_id,
            context=self.context,
        )

    def test_schedule_once(self):
        future = (timezone.now() + timedelta(hours=2)).isoformat()
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Say hello later',
            at=future,
        ))
        self.assertIn('Scheduled one-time', result)
        self.assertEqual(ScheduledMessage.objects.filter(
            conversation=self.conversation,
            kind=ScheduledMessage.Kind.ONCE,
            is_active=True,
        ).count(), 1)

    def test_schedule_recurring(self):
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Recurring check-in',
            every='hours',
            interval=2,
        ))
        self.assertIn('Scheduled recurring', result)
        schedule = ScheduledMessage.objects.get(
            conversation=self.conversation,
            kind=ScheduledMessage.Kind.RECURRING,
        )
        self.assertEqual(schedule.recurrence_interval, 2)
        self.assertEqual(schedule.recurrence_unit, 'hours')

    def test_schedule_recurring_daily_converts_to_utc(self):
        """Regression: schedule_message used django.utils.timezone.utc which
        does not exist — must use datetime.timezone.utc for the conversion."""
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Daily standup',
            every='days',
            interval=1,
            at_time='09:00',
        ))
        self.assertIn('Scheduled recurring', result)
        schedule = ScheduledMessage.objects.get(
            conversation=self.conversation,
            kind=ScheduledMessage.Kind.RECURRING,
        )
        self.assertEqual(schedule.recurrence_unit, 'days')
        # next_run_at must be timezone-aware (UTC)
        self.assertIsNotNone(schedule.next_run_at.tzinfo)

    def test_schedule_recurring_weekly_converts_to_utc(self):
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Weekly sync',
            every='weeks',
            interval=1,
            at_time='14:00',
            on_day=0,
        ))
        self.assertIn('Scheduled recurring', result)
        schedule = ScheduledMessage.objects.get(
            conversation=self.conversation,
            kind=ScheduledMessage.Kind.RECURRING,
        )
        self.assertEqual(schedule.recurrence_unit, 'weeks')
        self.assertIsNotNone(schedule.next_run_at.tzinfo)

    def test_schedule_recurring_monthly_converts_to_utc(self):
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Monthly report',
            every='months',
            interval=1,
            at_time='10:00',
            on_day=15,
        ))
        self.assertIn('Scheduled recurring', result)
        schedule = ScheduledMessage.objects.get(
            conversation=self.conversation,
            kind=ScheduledMessage.Kind.RECURRING,
        )
        self.assertEqual(schedule.recurrence_unit, 'months')
        self.assertIsNotNone(schedule.next_run_at.tzinfo)

    def test_schedule_rejects_past_datetime(self):
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Too late',
            at=past,
        ))
        self.assertIn('Error', result)
        self.assertIn('future', result)

    def test_schedule_rejects_both_at_and_every(self):
        future = (timezone.now() + timedelta(hours=2)).isoformat()
        result = self._call('schedule_message', ScheduleMessageParams(
            prompt='Conflicting',
            at=future,
            every='hours',
        ))
        self.assertIn('Error', result)
        self.assertIn('not both', result)

    def test_cancel_schedule(self):
        schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='To be cancelled',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() + timedelta(hours=1),
        )
        result = self._call('cancel_schedule', CancelScheduleParams(
            schedule_id=str(schedule.uuid),
        ))
        self.assertIn('Cancelled', result)
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_list_schedules_empty(self):
        result = self._call('list_schedules', {})
        self.assertIn('No active schedules', result)

    def test_list_schedules_with_entries(self):
        ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='First schedule',
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=timezone.now() + timedelta(hours=1),
        )
        ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.user,
            prompt='Second schedule',
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit='days',
            recurrence_interval=1,
            next_run_at=timezone.now() + timedelta(days=1),
        )
        result = self._call('list_schedules', {})
        self.assertIn('Active schedules (2)', result)
        self.assertIn('First schedule', result)
        self.assertIn('Second schedule', result)
