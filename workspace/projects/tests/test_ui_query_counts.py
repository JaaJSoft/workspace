from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.projects.models import Project, Sprint, TaskStatus
from workspace.projects.queries import project_users
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class AllTasksQueryCountTests(ProjectTestMixin, TestCase):
    """The all-tasks list renders ``task.sprint`` for every row, so the
    sprint has to travel with the task query."""

    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Scrummy", project_type=Project.Type.SCRUM
        )
        self.sprint = Sprint.objects.create(
            project=self.scrum, name="S1", state=Sprint.State.ACTIVE
        )
        self.url = reverse("projects_ui:all_tasks", args=[self.scrum.uuid])
        self.client.force_login(self.admin)

    def _add_tasks(self, count):
        status = self.scrum.statuses.filter(category=TaskStatus.Category.ACTIVE).first()
        for i in range(count):
            task = create_task(self.scrum, self.admin, title=f"T{i}", status=status)
            task.sprint = self.sprint
            task.save(update_fields=["sprint"])

    def test_sprint_is_not_fetched_once_per_task(self):
        self._add_tasks(5)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        # A standalone SELECT on the sprint table means a row resolved its FK
        # lazily; select_related folds it into the task query's JOIN instead.
        sprint_queries = [
            q for q in ctx.captured_queries if 'FROM "projects_sprint"' in q["sql"]
        ]
        self.assertEqual(
            len(sprint_queries),
            0,
            f"sprint must ride along with the tasks, got {len(sprint_queries)} "
            f"extra queries for 5 tasks",
        )

    def test_query_count_does_not_scale_with_task_count(self):
        self._add_tasks(2)
        self.client.get(self.url)  # warm the per-user settings cache

        with CaptureQueriesContext(connection) as ctx_baseline:
            self.client.get(self.url)
        baseline = len(ctx_baseline)

        self._add_tasks(10)

        with CaptureQueriesContext(connection) as ctx_after:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(ctx_after),
            baseline,
            msg=(
                f"Query count must not scale with task count - "
                f"baseline={baseline}, after adding 10 tasks={len(ctx_after)}"
            ),
        )


class TaskDeepLinkQueryCountTests(ProjectTestMixin, TestCase):
    """``?task=`` renders the panel inside the page shell; both halves need
    the project's user list and must share one resolution of it."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Deep")
        self.client.force_login(self.admin)

    def test_project_users_resolved_once_for_a_deep_link(self):
        url = reverse("projects_ui:project", args=[self.project.uuid])

        # Patched on the view module: that is the name the views resolve.
        with patch(
            "workspace.projects.ui.views.project_users", wraps=project_users
        ) as spy:
            response = self.client.get(f"{url}?task={self.task.uuid}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("panel_task", response.context)
        self.assertEqual(
            spy.call_count,
            1,
            f"project_users costs two queries; it ran {spy.call_count} times",
        )
