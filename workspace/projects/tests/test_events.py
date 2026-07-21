from django.test import TestCase

from workspace.projects.models import TaskEvent
from workspace.projects.tests.base import ProjectTestMixin


class TaskEventModelTests(ProjectTestMixin, TestCase):
    def test_event_row_with_snapshots(self):
        event = TaskEvent.objects.create(
            project=self.project,
            task=None,
            task_title="Ship the launch page",
            actor=self.admin,
            type=TaskEvent.Type.MOVED,
            from_status="To do",
            to_status="In progress",
        )
        event.refresh_from_db()
        self.assertEqual(event.project, self.project)
        self.assertIsNone(event.task)
        self.assertEqual(event.task_title, "Ship the launch page")
        self.assertEqual(event.type, TaskEvent.Type.MOVED)
        self.assertEqual(event.icon, "move-right")
        self.assertEqual(event.short_label, "Task moved")

    def test_icon_and_label_cover_all_types(self):
        for event_type in TaskEvent.Type:
            event = TaskEvent(type=event_type, task_title="x")
            self.assertTrue(event.icon)
            self.assertTrue(event.short_label)

    def test_ordering_newest_first(self):
        first = TaskEvent.objects.create(
            project=self.project, task_title="a", type=TaskEvent.Type.CREATED
        )
        second = TaskEvent.objects.create(
            project=self.project, task_title="b", type=TaskEvent.Type.CREATED
        )
        self.assertEqual(list(TaskEvent.objects.all()), [second, first])
