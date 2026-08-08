from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import TaskStatus
from workspace.projects.services.members import remove_member
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ProjectsPendingActionProviderTests(ProjectTestMixin, TestCase):
    """The badge counts open tasks assigned to the user, overdue or due today."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _action(self, user):
        from workspace.core.module_registry import registry

        return registry.get_pending_actions(user)["projects"]

    def _counts(self, user):
        return {"projects": self._action(user).count}

    def _make_task(self, *, due_date, assignees=None, status=None, project=None):
        return create_task(
            project or self.project,
            self.admin,
            title="Task",
            due_date=due_date,
            assignees=assignees if assignees is not None else [self.member],
            status=status,
        )

    def test_counts_overdue_task(self):
        self._make_task(due_date=self.today - timedelta(days=3))
        self.assertEqual(self._counts(self.member).get("projects"), 1)

    def test_counts_task_due_today(self):
        self._make_task(due_date=self.today)
        self.assertEqual(self._counts(self.member).get("projects"), 1)

    def test_excludes_task_due_tomorrow(self):
        self._make_task(due_date=self.today + timedelta(days=1))
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_excludes_task_without_due_date(self):
        self._make_task(due_date=None)
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_excludes_done_task(self):
        done = self.project.statuses.get(category=TaskStatus.Category.DONE)
        self._make_task(due_date=self.today - timedelta(days=1), status=done)
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_excludes_task_assigned_to_someone_else(self):
        self._make_task(due_date=self.today - timedelta(days=1), assignees=[self.admin])
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_excludes_archived_project(self):
        self._make_task(due_date=self.today - timedelta(days=1))
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_excludes_project_the_user_left(self):
        self._make_task(due_date=self.today - timedelta(days=1))
        remove_member(self.membership)
        self.assertEqual(self._counts(self.member).get("projects"), 0)

    def test_deep_links_to_the_single_pending_task(self):
        task = self._make_task(due_date=self.today)
        action = self._action(self.member)
        self.assertEqual(action.count, 1)
        self.assertEqual(action.url, f"/projects/{self.project.uuid}?task={task.uuid}")

    def test_has_no_target_with_several_pending_tasks(self):
        self._make_task(due_date=self.today)
        self._make_task(due_date=self.today - timedelta(days=1))
        action = self._action(self.member)
        self.assertEqual(action.count, 2)
        self.assertIsNone(action.url)
