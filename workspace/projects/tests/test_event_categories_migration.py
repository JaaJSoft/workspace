from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from workspace.projects.models import TaskEvent, TaskStatus
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task, move_tasks

from .base import ProjectTestMixin

backfill_categories = import_module(
    "workspace.projects.migrations.0027_taskevent_categories"
).backfill_categories


class _SchemaEditor:
    connection = connection


class EventCategoriesMigrationTests(ProjectTestMixin, TestCase):
    """The backfill runs on the final schema, so it is testable by blanking
    the categories to simulate history written before the snapshot."""

    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.task = create_task(self.project, self.admin, title="Work")
        move_tasks(self.project, self.todo, [self.task.uuid])
        move_tasks(self.project, self.done, [self.task.uuid])

    def _simulate_legacy_state(self):
        TaskEvent.objects.update(from_category="", to_category="")

    def _events(self):
        return {
            e.type: e
            for e in TaskEvent.objects.filter(task=self.task).order_by("created_at")
        }

    def test_resolves_both_sides_through_the_current_columns(self):
        self._simulate_legacy_state()

        backfill_categories(apps, _SchemaEditor())

        events = self._events()
        self.assertEqual(events["created"].to_category, TaskStatus.Category.BACKLOG)
        self.assertEqual(events["moved"].from_category, TaskStatus.Category.BACKLOG)
        self.assertEqual(events["moved"].to_category, TaskStatus.Category.ACTIVE)
        self.assertEqual(events["completed"].from_category, TaskStatus.Category.ACTIVE)
        self.assertEqual(events["completed"].to_category, TaskStatus.Category.DONE)

    def test_a_renamed_column_leaves_its_old_name_unresolved(self):
        self._simulate_legacy_state()
        self.todo.name = "Ready"
        self.todo.save(update_fields=["name"])

        backfill_categories(apps, _SchemaEditor())

        moved = self._events()["moved"]
        self.assertEqual(moved.from_category, TaskStatus.Category.BACKLOG)
        self.assertEqual(moved.to_category, "")

    def test_a_completion_into_a_vanished_column_is_still_done(self):
        self._simulate_legacy_state()
        TaskEvent.objects.filter(type="completed").update(to_status="Shipped")

        backfill_categories(apps, _SchemaEditor())

        self.assertEqual(
            self._events()["completed"].to_category, TaskStatus.Category.DONE
        )

    def test_events_without_a_status_stay_blank(self):
        self._simulate_legacy_state()
        TaskEvent.objects.create(
            project=self.project, task_title="Gone", type=TaskEvent.Type.COMPLETED
        )

        backfill_categories(apps, _SchemaEditor())

        orphan = TaskEvent.objects.get(task_title="Gone")
        self.assertEqual(orphan.to_category, "")

    def test_names_resolve_within_their_own_project(self):
        other = create_project(self.admin, name="Other")
        other_todo = other.statuses.get(name="To do")
        other_todo.category = TaskStatus.Category.BACKLOG
        other_todo.save(update_fields=["category"])
        self._simulate_legacy_state()

        backfill_categories(apps, _SchemaEditor())

        self.assertEqual(
            self._events()["moved"].to_category, TaskStatus.Category.ACTIVE
        )
