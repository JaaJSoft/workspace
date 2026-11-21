"""A deployment whose tasks can never reach a worker refuses to start."""

import importlib
import sys

from celery.signals import beat_init, celeryd_init
from django.core.checks.registry import registry
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from workspace.core.checks import check_task_broker

IN_MEMORY = {"CELERY_BROKER_URL": "memory://", "CELERY_TASK_ALWAYS_EAGER": False}


class TaskBrokerCheckTests(SimpleTestCase):
    @override_settings(**IN_MEMORY)
    def test_in_memory_broker_without_eager_mode_is_an_error(self):
        self.assertEqual([e.id for e in check_task_broker(None)], ["core.E001"])

    def test_the_check_is_registered(self):
        self.assertIn(check_task_broker, registry.get_checks())

    @override_settings(CELERY_BROKER_URL="memory://", CELERY_TASK_ALWAYS_EAGER=True)
    def test_eager_mode_runs_tasks_inline(self):
        self.assertEqual(check_task_broker(None), [])

    @override_settings(
        CELERY_BROKER_URL="redis://redis:6379/2", CELERY_TASK_ALWAYS_EAGER=False
    )
    def test_a_shared_broker_is_fine(self):
        self.assertEqual(check_task_broker(None), [])


class CeleryProcessesRefuseTests(SimpleTestCase):
    """Through the signals themselves: Celery swallows an Exception raised by a
    handler, so only a SystemExit actually stops the process."""

    @override_settings(**IN_MEMORY)
    def test_the_worker_refuses_to_start(self):
        with self.assertRaises(SystemExit) as raised:
            celeryd_init.send(sender="celery@test", instance=None, conf=None)
        self.assertIn("REDIS_URL", str(raised.exception))

    @override_settings(**IN_MEMORY)
    def test_the_scheduler_refuses_to_start(self):
        with self.assertRaises(SystemExit) as raised:
            beat_init.send(sender=None)
        self.assertIn("REDIS_URL", str(raised.exception))

    @override_settings(
        CELERY_BROKER_URL="redis://redis:6379/2", CELERY_TASK_ALWAYS_EAGER=False
    )
    def test_both_start_on_a_shared_broker(self):
        celeryd_init.send(sender="celery@test", instance=None, conf=None)
        beat_init.send(sender=None)


class WebServerRefusesTests(SimpleTestCase):
    def setUp(self):
        self.loaded = sys.modules.pop("workspace.wsgi", None)

    def tearDown(self):
        if self.loaded is not None:
            sys.modules["workspace.wsgi"] = self.loaded

    @override_settings(**IN_MEMORY)
    def test_the_wsgi_application_refuses_to_load(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "REDIS_URL"):
            importlib.import_module("workspace.wsgi")
