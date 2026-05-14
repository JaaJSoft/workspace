from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.ai.services.dispatch import _enqueue_worker, dispatch

User = get_user_model()


class DispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dispatch_user', password='pass123')

    @patch('workspace.ai.tasks.summarize.delay')
    def test_dispatch_creates_task_and_enqueues_worker(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={'message_id': 'abc'},
        )

        self.assertIsInstance(ai_task, AITask)
        self.assertEqual(ai_task.owner, self.user)
        self.assertEqual(ai_task.task_type, AITask.TaskType.SUMMARIZE)
        self.assertEqual(ai_task.input_data, {'message_id': 'abc'})
        self.assertEqual(ai_task.status, AITask.Status.PENDING)
        mock_delay.assert_called_once_with(str(ai_task.uuid))

    @patch('workspace.ai.tasks.summarize.delay')
    def test_dispatch_defaults_input_data_to_empty_dict(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
        )
        self.assertEqual(ai_task.input_data, {})
        mock_delay.assert_called_once_with(str(ai_task.uuid))

    @patch('workspace.ai.tasks.compose_email.delay')
    def test_reply_and_compose_share_compose_email_worker(self, mock_delay):
        compose_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.COMPOSE,
            input_data={'instructions': 'x'},
        )
        reply_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.REPLY,
            input_data={'message_id': 'y', 'instructions': 'z'},
        )

        self.assertEqual(mock_delay.call_count, 2)
        mock_delay.assert_any_call(str(compose_task.uuid))
        mock_delay.assert_any_call(str(reply_task.uuid))

    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    def test_classify_dispatch(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': ['u1', 'u2']},
        )
        mock_delay.assert_called_once_with(str(ai_task.uuid))

    @patch('workspace.ai.tasks.editor_action.delay')
    def test_editor_dispatch(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.EDITOR,
            input_data={'action': 'rewrite', 'content': 'hello'},
        )
        mock_delay.assert_called_once_with(str(ai_task.uuid))

    def test_dispatch_unknown_task_type_raises(self):
        # Create a task with a recognized type so the row is valid, then
        # mutate to a bogus type to force the dispatch-side validation
        # to fire (the model's TextChoices is informational, not enforced).
        with self.assertRaises(ValueError) as cm:
            ai_task = AITask(
                owner=self.user,
                task_type='not-a-real-type',
                input_data={},
            )
            _enqueue_worker(ai_task)

        self.assertIn('not-a-real-type', str(cm.exception))

    @patch('workspace.ai.tasks.extract_from_mail_messages.delay')
    def test_extract_dispatch(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.EXTRACT,
            input_data={'message_uuids': ['u1']},
        )
        mock_delay.assert_called_once_with(str(ai_task.uuid))

    def test_every_task_type_has_a_worker(self):
        # Catches the case where a new TaskType is added to the model
        # enum but the dispatch mapping is not updated. ``CHAT`` is
        # intentionally not routed through this service: chat responses
        # have their own dispatch path (see scheduled.py /
        # generate_chat_response).
        non_dispatched = {AITask.TaskType.CHAT}
        with (
            patch('workspace.ai.tasks.summarize.delay'),
            patch('workspace.ai.tasks.compose_email.delay'),
            patch('workspace.ai.tasks.classify_mail_messages.delay'),
            patch('workspace.ai.tasks.editor_action.delay'),
            patch('workspace.ai.tasks.extract_from_mail_messages.delay'),
        ):
            for task_type in AITask.TaskType.values:
                if task_type in non_dispatched:
                    continue
                ai_task = AITask(
                    owner=self.user,
                    task_type=task_type,
                    input_data={},
                )
                try:
                    _enqueue_worker(ai_task)
                except ValueError as e:
                    self.fail(f'TaskType {task_type!r} has no worker mapping: {e}')
