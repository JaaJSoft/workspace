from importlib import import_module

from django.apps import apps
from django.db.models import F
from django.test import TestCase

from workspace.projects.models import Project, Task
from workspace.projects.services.projects import create_project
from workspace.projects.services.references import KEY_RE
from workspace.projects.services.tasks import create_task, delete_task
from workspace.projects.tests.base import ProjectTestMixin

backfill = import_module(
    "workspace.projects.migrations.0010_backfill_task_references"
).backfill


class BackfillReferencesTests(ProjectTestMixin, TestCase):
    """The backfill recomputes numbers/keys from scratch, which makes it
    testable on the final schema: shift live numbers out of the way to
    simulate the legacy state (real legacy rows hold NULL, which the final
    schema forbids)."""

    def _simulate_legacy_state(self):
        Task.objects.update(number=F("number") + 1000)
        Project.objects.update(next_task_number=1)

    def test_numbers_follow_creation_order(self):
        t1 = create_task(self.project, self.admin, title="first")
        t2 = create_task(self.project, self.admin, title="second")
        t3 = create_task(self.project, self.admin, title="third")
        delete_task(t2, actor=self.admin)
        self._simulate_legacy_state()

        backfill(apps, None)

        t1.refresh_from_db()
        t3.refresh_from_db()
        self.assertEqual(t1.number, 1)
        self.assertEqual(t3.number, 2)
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual(project.next_task_number, 3)
        self.assertRegex(project.key, KEY_RE)

    def test_event_snapshots_updated_for_live_tasks(self):
        task = create_task(self.project, self.admin, title="first")
        self._simulate_legacy_state()
        backfill(apps, None)
        self.assertEqual(task.events.get().task_number, 1)

    def test_keys_stay_unique_for_same_name_projects(self):
        create_project(self.admin, name="Website")
        self._simulate_legacy_state()
        backfill(apps, None)
        keys = list(Project.objects.values_list("key", flat=True))
        self.assertEqual(len(keys), len(set(keys)))
