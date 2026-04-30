"""Tests for Celery-side Prometheus instrumentation defined in workspace.celery.

Covers:
- task_prerun/task_postrun signal handlers update celery_task_duration_seconds
  and celery_tasks_total{task,state}.
- The custom queue-length collector exposes celery_queue_length{queue} when the
  broker is Redis, and stays silent when it isn't.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from prometheus_client import REGISTRY

from workspace import celery as celery_app_module


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class TaskSignalTests(TestCase):
    def test_postrun_records_duration_and_increments_total_with_state(self):
        task = SimpleNamespace(name='workspace.fake.tasks.demo')
        labels = {'task': task.name, 'state': 'success'}
        before_total = _sample('celery_tasks_total', labels)
        before_count = _sample('celery_task_duration_seconds_count', {'task': task.name})

        celery_app_module._on_task_prerun(task_id='abc', task=task)
        celery_app_module._on_task_postrun(task_id='abc', task=task, state='SUCCESS')

        self.assertEqual(_sample('celery_tasks_total', labels) - before_total, 1)
        after_count = _sample('celery_task_duration_seconds_count', {'task': task.name})
        self.assertEqual(after_count - before_count, 1)
        # Start map must be cleared so the dict can't grow unbounded.
        self.assertNotIn('abc', celery_app_module._task_starts)

    def test_postrun_without_prior_prerun_still_increments_total(self):
        # Worker crash recovery scenario: a postrun arrives without a recorded start.
        # We must not raise, and we must still bump the counter so the failure is visible.
        task = SimpleNamespace(name='workspace.fake.tasks.orphan')
        labels = {'task': task.name, 'state': 'failure'}
        before = _sample('celery_tasks_total', labels)

        celery_app_module._on_task_postrun(task_id='no-start', task=task, state='FAILURE')

        self.assertEqual(_sample('celery_tasks_total', labels) - before, 1)

    def test_state_label_is_lowercased(self):
        # celery.states uses uppercase ('SUCCESS', 'FAILURE', 'RETRY'); we expose
        # them lowercase to match the convention used by other counters in the app.
        task = SimpleNamespace(name='workspace.fake.tasks.retried')
        celery_app_module._on_task_prerun(task_id='r1', task=task)
        celery_app_module._on_task_postrun(task_id='r1', task=task, state='RETRY')

        self.assertGreater(
            _sample('celery_tasks_total', {'task': task.name, 'state': 'retry'}),
            0,
        )


class QueueLengthCollectorTests(TestCase):
    def test_collector_emits_no_sample_for_non_redis_broker(self):
        collector = celery_app_module._CeleryQueueLengthCollector()
        with self.settings(CELERY_BROKER_URL='memory://'):
            families = list(collector.collect())
        # No yield expected at all when broker is non-Redis.
        self.assertEqual(families, [])

    def test_collector_emits_one_sample_per_queue_via_llen(self):
        collector = celery_app_module._CeleryQueueLengthCollector()

        fake_client = MagicMock()
        fake_client.llen.side_effect = lambda name: {'celery': 3, 'priority': 7}[name]

        import kombu
        queues = [kombu.Queue('celery'), kombu.Queue('priority')]

        with self.settings(
            CELERY_BROKER_URL='redis://localhost:6379/0',
            CELERY_TASK_QUEUES=queues,
        ), patch('redis.Redis.from_url', return_value=fake_client):
            families = list(collector.collect())

        self.assertEqual(len(families), 1)
        family = families[0]
        self.assertEqual(family.name, 'celery_queue_length')
        # Build a dict from the samples for stable lookup regardless of ordering.
        samples = {s.labels['queue']: s.value for s in family.samples}
        self.assertEqual(samples, {'celery': 3.0, 'priority': 7.0})

    def test_collector_swallows_redis_errors(self):
        collector = celery_app_module._CeleryQueueLengthCollector()

        fake_client = MagicMock()
        fake_client.llen.side_effect = RuntimeError('redis down')

        with self.settings(CELERY_BROKER_URL='redis://localhost:6379/0'), \
                patch('redis.Redis.from_url', return_value=fake_client):
            families = list(collector.collect())

        # We yielded a (possibly empty) family — the scrape must not crash.
        self.assertEqual(len(families), 1)
        self.assertEqual(list(families[0].samples), [])
