"""Tests for ai.editor_action (workspace.ai.tasks.editor)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.ai.tasks.editor import editor_action

User = get_user_model()


class EditorActionTaskTests(TestCase):
    """Behavioural tests for the editor_action Celery task."""

    def setUp(self):
        self.user = User.objects.create_user(username='editoruser', password='pw')

    def _make_task(self, **input_data):
        defaults = {
            'action': 'improve',
            'content': 'hello world',
            'language': 'en',
            'filename': 'note.md',
        }
        defaults.update(input_data)
        return AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data=defaults,
        )

    def test_unknown_action_marks_task_failed_without_raising(self):
        """In-band validation failure (bad action name): the AITask row is
        FAILED for the API to surface, but the task itself completes
        cleanly so Celery does not record it as crashed."""
        task = self._make_task(action='nonsense')
        result = editor_action(str(task.uuid))
        self.assertEqual(result['status'], 'error')
        self.assertIn('Unknown action', result['error'])

        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.FAILED)
        self.assertIn('Unknown action', task.error)

    def test_task_not_found_returns_error_without_raising(self):
        """Missing AITask row is also a clean return - the dispatcher passed
        a stale UUID, the worker should not crash on that."""
        result = editor_action('00000000-0000-0000-0000-000000000000')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'Task not found')

    def test_llm_failure_propagates_so_celery_marks_task_failed(self):
        """Regression: when call_llm raises, the exception MUST propagate out
        of editor_action. Returning an error dict instead of re-raising would
        make Celery record the task as success in its result backend
        (``celery_tasks_total{state='success'}`` would tick) even though the
        work actually failed - breaking Flower / monitoring.

        ai_task_lifecycle still marks the AITask row FAILED before
        re-raising, so the API surface is unchanged."""
        task = self._make_task(action='improve')

        with patch('workspace.ai.tasks.editor.call_llm') as mock_call:
            mock_call.side_effect = RuntimeError('LLM crashed')
            with self.assertRaises(RuntimeError):
                editor_action(str(task.uuid))

        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.FAILED)
        self.assertEqual(task.error, 'LLM crashed')
