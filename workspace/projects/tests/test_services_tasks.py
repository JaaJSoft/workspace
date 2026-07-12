from django.test import TestCase

from workspace.projects.services.tasks import (
    apply_status_change,
    create_task,
    reorder_tasks,
)

from .base import ProjectTestMixin


class TaskServiceMixin(ProjectTestMixin):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")


class CreateTaskTests(TaskServiceMixin, TestCase):
    def test_defaults_to_backlog_status_at_end(self):
        t1 = create_task(self.project, self.admin, title="one")
        t2 = create_task(self.project, self.admin, title="two")
        self.assertEqual(t1.status, self.backlog)
        self.assertEqual((t1.position, t2.position), (0, 1))
        self.assertEqual(t1.created_by, self.admin)

    def test_position_is_per_status(self):
        create_task(self.project, self.admin, title="backlog one")
        in_todo = create_task(
            self.project, self.admin, title="todo one", status=self.todo
        )
        self.assertEqual(in_todo.position, 0)

    def test_created_directly_in_done_gets_completed_at(self):
        task = create_task(self.project, self.admin, title="done", status=self.done)
        self.assertIsNotNone(task.completed_at)

    def test_assignees_and_labels_set(self):
        label = self.project.labels.create(name="bug", color="#ff0000")
        task = create_task(
            self.project,
            self.admin,
            title="t",
            assignees=[self.member],
            labels=[label],
        )
        self.assertEqual(list(task.assignees.all()), [self.member])
        self.assertEqual(list(task.labels.all()), [label])


class ApplyStatusChangeTests(TaskServiceMixin, TestCase):
    def test_moving_to_done_sets_completed_at_and_appends(self):
        create_task(self.project, self.admin, title="already", status=self.done)
        task = create_task(self.project, self.admin, title="t")
        task.status = self.done
        apply_status_change(task)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.position, 1)

    def test_moving_out_of_done_clears_completed_at(self):
        task = create_task(self.project, self.admin, title="t", status=self.done)
        task.status = self.todo
        apply_status_change(task)
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)
        self.assertEqual(task.status, self.todo)


class ReorderTasksTests(TaskServiceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t1 = create_task(self.project, self.admin, title="t1")
        self.t2 = create_task(self.project, self.admin, title="t2")
        self.t3 = create_task(self.project, self.admin, title="t3")

    def _backlog_titles(self):
        return [
            t.title
            for t in self.project.tasks.filter(status=self.backlog).order_by(
                "position", "created_at"
            )
        ]

    def test_reorder_within_status(self):
        reorder_tasks(
            self.project, self.backlog, [self.t3.uuid, self.t1.uuid, self.t2.uuid]
        )
        self.assertEqual(self._backlog_titles(), ["t3", "t1", "t2"])

    def test_reorder_is_idempotent(self):
        order = [self.t3.uuid, self.t1.uuid, self.t2.uuid]
        reorder_tasks(self.project, self.backlog, order)
        reorder_tasks(self.project, self.backlog, order)
        self.assertEqual(self._backlog_titles(), ["t3", "t1", "t2"])

    def test_unlisted_tasks_keep_relative_order_after_listed(self):
        reorder_tasks(self.project, self.backlog, [self.t3.uuid])
        self.assertEqual(self._backlog_titles(), ["t3", "t1", "t2"])

    def test_cross_status_move_sets_status_and_completed_at(self):
        reorder_tasks(self.project, self.done, [self.t1.uuid])
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, self.done)
        self.assertIsNotNone(self.t1.completed_at)
        self.assertEqual(self._backlog_titles(), ["t2", "t3"])

    def test_move_back_out_of_done_clears_completed_at(self):
        reorder_tasks(self.project, self.done, [self.t1.uuid])
        reorder_tasks(
            self.project, self.backlog, [self.t1.uuid, self.t2.uuid, self.t3.uuid]
        )
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.completed_at)
        self.assertEqual(self.t1.status, self.backlog)

    def test_unknown_uuids_silently_skipped(self):
        import uuid as uuid_module

        reorder_tasks(self.project, self.backlog, [uuid_module.uuid4(), self.t2.uuid])
        self.assertEqual(self._backlog_titles(), ["t2", "t1", "t3"])
