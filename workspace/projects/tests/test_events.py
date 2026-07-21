from django.test import TestCase

from workspace.projects.models import TaskEvent
from workspace.projects.tests.base import ProjectTestMixin
from workspace.projects.services.events import (
    events_for_project,
    move_event_type,
    record_task_event,
)
from workspace.projects.services.tasks import (
    apply_status_change,
    create_task,
    delete_task,
    reorder_tasks,
)


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


class CreateTaskEventTests(ProjectTestMixin, TestCase):
    def test_create_task_records_created_event(self):
        task = create_task(self.project, self.admin, title="New landing page")
        event = TaskEvent.objects.get(task=task)
        self.assertEqual(event.type, TaskEvent.Type.CREATED)
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.task_title, "New landing page")
        self.assertEqual(event.to_status, "Backlog")
        self.assertEqual(event.from_status, "")

    def test_create_directly_in_done_still_records_created(self):
        done = self.project.statuses.get(name="Done")
        task = create_task(self.project, self.admin, title="Hotfix", status=done)
        event = TaskEvent.objects.get(task=task)
        self.assertEqual(event.type, TaskEvent.Type.CREATED)
        self.assertEqual(event.to_status, "Done")
        self.assertIsNotNone(task.completed_at)


class StatusChangeEventTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.task = create_task(
            self.project, self.admin, title="Ship it", status=self.todo
        )
        TaskEvent.objects.all().delete()

    def test_move_to_active_column_records_moved(self):
        in_progress = self.project.statuses.get(name="In progress")
        old_status = self.task.status
        self.task.status = in_progress
        apply_status_change(self.task, actor=self.member, old_status=old_status)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.MOVED)
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.from_status, "To do")
        self.assertEqual(event.to_status, "In progress")

    def test_move_to_done_records_completed(self):
        old_status = self.task.status
        self.task.status = self.done
        apply_status_change(self.task, actor=self.admin, old_status=old_status)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.COMPLETED)

    def test_api_status_patch_records_event_with_actor(self):
        self.client.force_login(self.member)
        resp = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}",
            {"status": str(self.done.uuid)},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.COMPLETED)
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.from_status, "To do")

    def test_patch_without_status_change_records_nothing(self):
        self.client.force_login(self.member)
        resp = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}",
            {"title": "Ship it soon"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TaskEvent.objects.count(), 0)


class DeleteTaskEventTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Old idea")
        TaskEvent.objects.all().delete()

    def test_delete_task_records_event_with_surviving_snapshot(self):
        delete_task(self.task, actor=self.admin)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.DELETED)
        self.assertIsNone(event.task)  # FK nulled by the delete
        self.assertEqual(event.task_title, "Old idea")
        self.assertEqual(event.actor, self.admin)

    def test_api_delete_records_event(self):
        self.client.force_login(self.admin)
        resp = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}"
        )
        self.assertEqual(resp.status_code, 204)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.DELETED)
        self.assertEqual(event.actor, self.admin)


class ReorderEventTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.task = create_task(
            self.project, self.admin, title="Drag me", status=self.todo
        )
        TaskEvent.objects.all().delete()

    def test_cross_column_drop_records_completed(self):
        reorder_tasks(self.project, self.done, [self.task.uuid], actor=self.member)
        event = TaskEvent.objects.get()
        self.assertEqual(event.type, TaskEvent.Type.COMPLETED)
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.from_status, "To do")
        self.assertEqual(event.to_status, "Done")

    def test_in_column_shuffle_records_nothing(self):
        other = create_task(
            self.project, self.admin, title="Neighbor", status=self.todo
        )
        TaskEvent.objects.all().delete()
        reorder_tasks(
            self.project,
            self.todo,
            [other.uuid, self.task.uuid],
            actor=self.admin,
        )
        self.assertEqual(TaskEvent.objects.count(), 0)

    def test_replay_is_idempotent_for_events(self):
        reorder_tasks(self.project, self.done, [self.task.uuid], actor=self.admin)
        reorder_tasks(self.project, self.done, [self.task.uuid], actor=self.admin)
        self.assertEqual(TaskEvent.objects.count(), 1)
