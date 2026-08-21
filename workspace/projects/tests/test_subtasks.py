from django.test import TestCase

from workspace.projects.models import Subtask
from workspace.projects.services.subtasks import create_subtask, reorder_subtasks
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class CreateSubtaskTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship the thing")

    def test_appends_at_the_end(self):
        first = create_subtask(self.task, "Write the code")
        second = create_subtask(self.task, "Write the tests")
        self.assertEqual(first.position, 0)
        self.assertEqual(second.position, 1)
        self.assertFalse(first.done)

    def test_positions_are_per_task(self):
        other = create_task(self.project, self.admin, title="Other task")
        create_subtask(self.task, "a")
        item = create_subtask(other, "b")
        self.assertEqual(item.position, 0)


class ReorderSubtasksTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship the thing")
        self.a = create_subtask(self.task, "a")
        self.b = create_subtask(self.task, "b")
        self.c = create_subtask(self.task, "c")

    def _titles(self):
        return [s.title for s in self.task.subtasks.order_by("position", "created_at")]

    def test_applies_full_order(self):
        reorder_subtasks(self.task, [self.c.uuid, self.a.uuid, self.b.uuid])
        self.assertEqual(self._titles(), ["c", "a", "b"])

    def test_unlisted_items_keep_their_relative_order_after_listed_ones(self):
        reorder_subtasks(self.task, [self.c.uuid])
        self.assertEqual(self._titles(), ["c", "a", "b"])

    def test_unknown_uuids_are_skipped(self):
        other_task = create_task(self.project, self.admin, title="Other")
        foreign = create_subtask(other_task, "foreign")
        reorder_subtasks(self.task, [foreign.uuid, self.b.uuid, self.a.uuid])
        self.assertEqual(self._titles(), ["b", "a", "c"])
        # The foreign item stays on its own task, untouched.
        foreign.refresh_from_db()
        self.assertEqual(foreign.task_id, other_task.uuid)
        self.assertEqual(foreign.position, 0)

    def test_replay_is_idempotent(self):
        order = [self.b.uuid, self.c.uuid, self.a.uuid]
        reorder_subtasks(self.task, order)
        reorder_subtasks(self.task, order)
        self.assertEqual(self._titles(), ["b", "c", "a"])
        positions = list(
            self.task.subtasks.order_by("position").values_list("position", flat=True)
        )
        self.assertEqual(positions, [0, 1, 2])

    def test_duplicate_uuids_count_once(self):
        reorder_subtasks(self.task, [self.b.uuid, self.b.uuid, self.a.uuid])
        self.assertEqual(self._titles(), ["b", "a", "c"])


class SubtaskModelTests(ProjectTestMixin, TestCase):
    def test_deleting_the_task_cascades(self):
        task = create_task(self.project, self.admin, title="Doomed")
        create_subtask(task, "item")
        task.delete()
        self.assertEqual(Subtask.objects.count(), 0)
