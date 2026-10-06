"""Worker-side behaviour of the Celery app defined in workspace.celery."""

from unittest.mock import call, patch

from celery.signals import celeryd_after_setup
from django.test import SimpleTestCase

from workspace import celery as celery_app


class FreezeBeforeForkTests(SimpleTestCase):
    def test_collects_then_freezes_once_the_worker_is_set_up(self):
        """Through the signal itself: the handler only helps if it runs before
        the pool forks, and celeryd_after_setup is what guarantees that."""
        with patch.object(celery_app, "gc") as gc:
            celeryd_after_setup.send(sender="celery@test", instance=None, conf=None)

        self.assertEqual(gc.mock_calls, [call.collect(), call.freeze()])
