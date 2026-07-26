from django.test import TestCase

from workspace.projects.models import TaskEvent, TaskStatus
from workspace.projects.services.statuses import (
    LastCategoryStatusError,
    StatusTargetError,
    create_status,
    delete_status,
    reorder_statuses,
)
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class CreateStatusTests(ProjectTestMixin, TestCase):
    def test_appends_at_end_of_list(self):
        # Default project ships 4 statuses at positions 0..3.
        status = create_status(
            self.project, name="Review", category=TaskStatus.Category.ACTIVE
        )
        self.assertEqual(status.position, 4)
        self.assertEqual(status.project, self.project)
        self.assertEqual(status.color, "")

    def test_stores_color(self):
        status = create_status(
            self.project,
            name="Review",
            category=TaskStatus.Category.ACTIVE,
            color="#22c55e",
        )
        self.assertEqual(status.color, "#22c55e")


class ReorderStatusesTests(ProjectTestMixin, TestCase):
    def _names_in_order(self):
        return list(
            self.project.statuses.order_by("position", "created_at").values_list(
                "name", flat=True
            )
        )

    def test_applies_full_order(self):
        statuses = {s.name: s for s in self.project.statuses.all()}
        reorder_statuses(
            self.project,
            [
                statuses["Done"].uuid,
                statuses["In progress"].uuid,
                statuses["To do"].uuid,
                statuses["Backlog"].uuid,
            ],
        )
        self.assertEqual(
            self._names_in_order(), ["Done", "In progress", "To do", "Backlog"]
        )

    def test_is_idempotent(self):
        statuses = {s.name: s for s in self.project.statuses.all()}
        order = [
            statuses["Done"].uuid,
            statuses["In progress"].uuid,
            statuses["To do"].uuid,
            statuses["Backlog"].uuid,
        ]
        reorder_statuses(self.project, order)
        reorder_statuses(self.project, order)
        self.assertEqual(
            self._names_in_order(), ["Done", "In progress", "To do", "Backlog"]
        )

    def test_unlisted_statuses_keep_relative_order_after_listed(self):
        statuses = {s.name: s for s in self.project.statuses.all()}
        reorder_statuses(self.project, [statuses["Done"].uuid])
        self.assertEqual(
            self._names_in_order(), ["Done", "Backlog", "To do", "In progress"]
        )

    def test_unknown_uuids_are_skipped(self):
        import uuid as uuid_module

        statuses = {s.name: s for s in self.project.statuses.all()}
        reorder_statuses(self.project, [uuid_module.uuid4(), statuses["Done"].uuid])
        self.assertEqual(self._names_in_order()[0], "Done")


class DeleteStatusTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.in_progress = self.project.statuses.get(name="In progress")
        self.done = self.project.statuses.get(name="Done")

    def test_refuses_last_column_of_category(self):
        with self.assertRaises(LastCategoryStatusError):
            delete_status(self.backlog)
        self.assertTrue(self.project.statuses.filter(pk=self.backlog.pk).exists())

    def test_empty_column_deletes_without_target(self):
        delete_status(self.todo)
        self.assertFalse(self.project.statuses.filter(pk=self.todo.pk).exists())

    def test_tasks_without_target_refused(self):
        create_task(self.project, self.admin, title="A", status=self.todo)
        with self.assertRaises(StatusTargetError):
            delete_status(self.todo)

    def test_target_must_differ_from_deleted(self):
        create_task(self.project, self.admin, title="A", status=self.todo)
        with self.assertRaises(StatusTargetError):
            delete_status(self.todo, move_to=self.todo)

    def test_target_must_belong_to_same_project(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        other_todo = other.statuses.get(name="To do")
        create_task(self.project, self.admin, title="A", status=self.todo)
        with self.assertRaises(StatusTargetError):
            delete_status(self.todo, move_to=other_todo)

    def test_moves_tasks_to_end_of_target(self):
        existing = create_task(
            self.project, self.admin, title="Existing", status=self.in_progress
        )
        a = create_task(self.project, self.admin, title="A", status=self.todo)
        b = create_task(self.project, self.admin, title="B", status=self.todo)
        delete_status(self.todo, move_to=self.in_progress, actor=self.admin)
        a.refresh_from_db()
        b.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(a.status, self.in_progress)
        self.assertEqual(b.status, self.in_progress)
        self.assertLess(existing.position, a.position)
        self.assertLess(a.position, b.position)

    def test_moving_into_done_sets_completed_at(self):
        task = create_task(self.project, self.admin, title="A", status=self.todo)
        self.assertIsNone(task.completed_at)
        delete_status(self.todo, move_to=self.done, actor=self.admin)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_moving_out_of_done_clears_completed_at(self):
        second_done = create_status(
            self.project, name="Archived done", category=TaskStatus.Category.DONE
        )
        task = create_task(self.project, self.admin, title="A", status=second_done)
        self.assertIsNotNone(task.completed_at)
        delete_status(second_done, move_to=self.todo, actor=self.admin)
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_records_move_events_with_name_snapshots(self):
        create_task(self.project, self.admin, title="A", status=self.todo)
        delete_status(self.todo, move_to=self.done, actor=self.admin)
        event = self.project.task_events.filter(type=TaskEvent.Type.COMPLETED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.from_status, "To do")
        self.assertEqual(event.to_status, "Done")
        self.assertEqual(event.actor, self.admin)
