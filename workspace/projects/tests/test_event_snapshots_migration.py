from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from workspace.projects.models import Project, TaskEvent, TaskStatus
from workspace.projects.services.projects import create_project
from workspace.projects.services.sprints import assign_tasks_to_sprint
from workspace.projects.services.tasks import create_task, delete_task, move_tasks

from .base import ProjectTestMixin

_migration = import_module("workspace.projects.migrations.0027_taskevent_snapshots")
backfill_categories = _migration.backfill_categories
backfill_sprint_refs = _migration.backfill_sprint_refs
backfill_creation_estimates = _migration.backfill_creation_estimates


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


class SprintRefsMigrationTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        self.sprint = self.scrum.sprints.create(name="Sprint 1")
        self.other = self.scrum.sprints.create(name="Sprint 2")

    def _simulate_legacy_state(self):
        TaskEvent.objects.update(from_ref=None, to_ref=None)

    def test_resolves_both_sides_by_name_within_the_project(self):
        task = create_task(self.scrum, self.admin, title="a", sprint=self.sprint)
        assign_tasks_to_sprint(self.scrum, self.other, [task.uuid], actor=self.admin)
        self._simulate_legacy_state()

        backfill_sprint_refs(apps, _SchemaEditor())

        joined, moved = TaskEvent.objects.filter(
            task=task, type=TaskEvent.Type.SPRINT
        ).order_by("created_at")
        self.assertEqual(joined.to_ref, self.sprint.pk)
        self.assertEqual(
            (moved.from_ref, moved.to_ref), (self.sprint.pk, self.other.pk)
        )

    def test_a_deleted_sprint_stays_unresolved(self):
        create_task(self.scrum, self.admin, title="a", sprint=self.sprint)
        self._simulate_legacy_state()
        self.sprint.delete()

        backfill_sprint_refs(apps, _SchemaEditor())

        self.assertIsNone(TaskEvent.objects.get(type=TaskEvent.Type.SPRINT).to_ref)

    def test_a_reused_name_does_not_claim_the_former_sprints_trail(self):
        create_task(self.scrum, self.admin, title="a", sprint=self.sprint)
        self._simulate_legacy_state()
        self.sprint.delete()
        reborn = self.scrum.sprints.create(name="Sprint 1")

        backfill_sprint_refs(apps, _SchemaEditor())

        event = TaskEvent.objects.get(type=TaskEvent.Type.SPRINT)
        self.assertIsNone(event.to_ref)
        self.assertNotEqual(event.to_ref, reborn.pk)

    def test_leaving_a_sprint_for_the_pool_has_no_target_ref(self):
        task = create_task(self.scrum, self.admin, title="a", sprint=self.sprint)
        assign_tasks_to_sprint(self.scrum, None, [task.uuid], actor=self.admin)
        self._simulate_legacy_state()

        backfill_sprint_refs(apps, _SchemaEditor())

        left = TaskEvent.objects.filter(type=TaskEvent.Type.SPRINT).latest("created_at")
        self.assertEqual((left.from_ref, left.to_ref), (self.sprint.pk, None))


class CreationEstimatesMigrationTests(ProjectTestMixin, TestCase):
    def _simulate_legacy_state(self):
        TaskEvent.objects.filter(type=TaskEvent.Type.CREATED).update(to_value="")

    def _created(self, task):
        return TaskEvent.objects.get(
            task_number=task.number, type=TaskEvent.Type.CREATED
        )

    def _record_change(self, task, before, after):
        TaskEvent.objects.create(
            project=self.project,
            task=task,
            task_title=task.title,
            task_number=task.number,
            type=TaskEvent.Type.ESTIMATED,
            from_value=before,
            to_value=after,
        )

    def test_a_never_re_estimated_task_takes_its_current_estimate(self):
        task = create_task(self.project, self.admin, title="a", estimate=Decimal("3.5"))
        self._simulate_legacy_state()

        backfill_creation_estimates(apps, _SchemaEditor())

        self.assertEqual(Decimal(self._created(task).to_value), Decimal("3.5"))

    def test_the_first_change_wins_over_the_current_value(self):
        task = create_task(self.project, self.admin, title="a", estimate=Decimal("8"))
        self._record_change(task, "8", "5")
        self._record_change(task, "5", "3")
        task.estimate = Decimal("3")
        task.save(update_fields=["estimate"])
        self._simulate_legacy_state()

        backfill_creation_estimates(apps, _SchemaEditor())

        self.assertEqual(self._created(task).to_value, "8")

    def test_a_task_born_unestimated_then_estimated_stays_blank(self):
        task = create_task(self.project, self.admin, title="a")
        self._record_change(task, "", "5")
        task.estimate = Decimal("5")
        task.save(update_fields=["estimate"])
        self._simulate_legacy_state()

        backfill_creation_estimates(apps, _SchemaEditor())

        self.assertEqual(self._created(task).to_value, "")

    def test_a_deleted_task_recovers_through_its_first_change_only(self):
        recoverable = create_task(
            self.project, self.admin, title="a", estimate=Decimal("2")
        )
        self._record_change(recoverable, "2", "4")
        lost = create_task(self.project, self.admin, title="b", estimate=Decimal("6"))
        self._simulate_legacy_state()
        delete_task(recoverable, actor=self.admin)
        delete_task(lost, actor=self.admin)

        backfill_creation_estimates(apps, _SchemaEditor())

        self.assertEqual(self._created(recoverable).to_value, "2")
        self.assertEqual(self._created(lost).to_value, "")

    def test_unestimated_tasks_are_left_alone(self):
        task = create_task(self.project, self.admin, title="a")
        self._simulate_legacy_state()

        backfill_creation_estimates(apps, _SchemaEditor())

        self.assertEqual(self._created(task).to_value, "")
