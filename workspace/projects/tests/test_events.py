from django.test import TestCase

from workspace.projects.models import TaskEvent
from workspace.projects.tests.base import ProjectTestMixin
from workspace.projects.services.events import (
    events_for_project,
    move_event_type,
    record_task_event,
)
from workspace.projects.services.tasks import create_task


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


class RecordTaskEventTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.task = create_task(self.project, self.admin, title="Write docs")
        TaskEvent.objects.all().delete()

    def test_record_snapshots_titles_and_status_names(self):
        event = record_task_event(
            self.task,
            type=TaskEvent.Type.MOVED,
            actor=self.member,
            from_status=self.todo,
            to_status=self.done,
        )
        event.refresh_from_db()
        self.assertEqual(event.project, self.project)
        self.assertEqual(event.task, self.task)
        self.assertEqual(event.task_title, "Write docs")
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.from_status, "To do")
        self.assertEqual(event.to_status, "Done")

    def test_record_without_statuses_leaves_blank(self):
        event = record_task_event(
            self.task, type=TaskEvent.Type.DELETED, actor=self.admin
        )
        self.assertEqual(event.from_status, "")
        self.assertEqual(event.to_status, "")

    def test_move_event_type_done_maps_to_completed(self):
        self.assertEqual(move_event_type(self.done), TaskEvent.Type.COMPLETED)
        self.assertEqual(move_event_type(self.todo), TaskEvent.Type.MOVED)

    def test_events_for_project_scopes_and_limits(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        other_task = create_task(other, self.admin, title="Elsewhere")
        TaskEvent.objects.all().delete()
        for i in range(3):
            record_task_event(self.task, type=TaskEvent.Type.MOVED, actor=None)
        record_task_event(other_task, type=TaskEvent.Type.CREATED, actor=None)

        events = list(events_for_project(self.project, limit=2))
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.project_id == self.project.uuid for e in events))
