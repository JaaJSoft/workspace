from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File, FileEvent
from workspace.files.services import event_dispatch
from workspace.files.services.event_dispatch import (
    has_handlers,
    on_file_event,
    run_handlers,
)
from workspace.files.services.events import record_event

User = get_user_model()


class DispatchRegistryTests(TestCase):
    def setUp(self):
        # Each test gets a clean registry so handlers don't leak between tests.
        self._saved = {k: list(v) for k, v in event_dispatch._HANDLERS.items()}

    def tearDown(self):
        event_dispatch._HANDLERS.clear()
        event_dispatch._HANDLERS.update(self._saved)

    def _make_event(self, action):
        user = User.objects.create_user(username="evt", password="p")
        f = File.objects.create(owner=user, name="a.txt", node_type=File.NodeType.FILE)
        return FileEvent.objects.create(file=f, actor=user, action=action)

    def test_registered_handler_runs_for_its_action(self):
        seen = []

        @on_file_event(FileEvent.Action.CREATED)
        def handler(event):
            seen.append(event.uuid)

        event = self._make_event(FileEvent.Action.CREATED)
        run_handlers(event.uuid)
        self.assertEqual(seen, [event.uuid])

    def test_handler_not_called_for_other_actions(self):
        seen = []

        @on_file_event(FileEvent.Action.CREATED)
        def handler(event):
            seen.append(event.uuid)

        event = self._make_event(FileEvent.Action.RENAMED)
        run_handlers(event.uuid)
        self.assertEqual(seen, [])

    def test_one_handler_raising_does_not_stop_others(self):
        seen = []

        @on_file_event(FileEvent.Action.CREATED)
        def bad(event):
            raise ValueError("boom")

        @on_file_event(FileEvent.Action.CREATED)
        def good(event):
            seen.append("ran")

        event = self._make_event(FileEvent.Action.CREATED)
        run_handlers(event.uuid)  # must not raise
        self.assertEqual(seen, ["ran"])

    def test_has_handlers(self):
        self.assertFalse(has_handlers(FileEvent.Action.MOVED))

        @on_file_event(FileEvent.Action.MOVED)
        def handler(event):
            pass

        self.assertTrue(has_handlers(FileEvent.Action.MOVED))

    def test_missing_event_is_silently_ignored(self):
        # CASCADE means a hard-deleted file's events vanish before the task runs.
        run_handlers("00000000-0000-0000-0000-000000000000")  # must not raise


class DispatchSchedulingTests(TestCase):
    def setUp(self):
        self._saved = {k: list(v) for k, v in event_dispatch._HANDLERS.items()}
        self.user = User.objects.create_user(username="sched", password="p")
        self.file = File.objects.create(
            owner=self.user, name="a.txt", node_type=File.NodeType.FILE
        )

    def tearDown(self):
        event_dispatch._HANDLERS.clear()
        event_dispatch._HANDLERS.update(self._saved)

    def test_scheduled_on_commit_when_handler_exists(self):
        @on_file_event(FileEvent.Action.CREATED)
        def handler(event):
            pass

        with patch("workspace.files.tasks.run_file_event_handlers") as task:
            with self.captureOnCommitCallbacks(execute=True):
                event = record_event(self.file, self.user, FileEvent.Action.CREATED)
            task.delay.assert_called_once_with(str(event.uuid))

    def test_not_scheduled_when_no_handler(self):
        with patch("workspace.files.tasks.run_file_event_handlers") as task:
            with self.captureOnCommitCallbacks(execute=True):
                record_event(self.file, self.user, FileEvent.Action.RENAMED)
            task.delay.assert_not_called()
