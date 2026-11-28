"""The task list read by a due-date window, the way the calendar overlay
reads it: open tasks due between two days, across projects."""

from datetime import date

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

URL = "/api/v1/projects/tasks"


class DueWindowTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")

    def _task(self, title, due_date, *, project=None, task_status=None):
        return create_task(
            project or self.project,
            self.admin,
            title=title,
            due_date=due_date,
            status=task_status or self.todo,
        )

    def _get(self, user=None, **params):
        self.client.force_authenticate(user or self.member)
        window = {
            "due_after": "2026-08-01",
            "due_before": "2026-08-31",
            "completed": "false",
            "ordering": "due_date",
        }
        return self.client.get(URL, {**window, **params})

    def _titles(self, **kwargs):
        response = self._get(**kwargs)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [task["title"] for task in response.data]

    def test_a_task_carries_what_a_calendar_entry_shows(self):
        task = self._task("Ship it", date(2026, 8, 15))
        (entry,) = self._get().data
        self.assertEqual(entry["uuid"], str(task.uuid))
        self.assertEqual(entry["due_date"], "2026-08-15")
        self.assertEqual(entry["reference"], f"{self.project.key}-{task.number}")
        self.assertEqual(entry["project"]["name"], self.project.name)
        self.assertEqual(
            entry["url"], f"/projects/{self.project.uuid}/board?task={task.uuid}"
        )
        self.assertEqual(
            entry["card_url"], f"/projects/{self.project.uuid}/tasks/{task.uuid}/card"
        )

    def test_both_ends_of_the_window_are_in(self):
        self._task("First day", date(2026, 8, 1))
        self._task("Last day", date(2026, 8, 31))
        self._task("Day after", date(2026, 9, 1))
        self._task("Day before", date(2026, 7, 31))
        self.assertEqual(self._titles(), ["First day", "Last day"])

    def test_undated_tasks_are_absent(self):
        create_task(self.project, self.admin, title="Someday")
        self.assertEqual(self._titles(), [])

    def test_completed_tasks_are_left_out(self):
        self._task("Open", date(2026, 8, 10))
        self._task("Shipped", date(2026, 8, 11), task_status=self.done)
        self.assertEqual(self._titles(), ["Open"])

    def test_archived_projects_are_left_out(self):
        archived = create_project(self.member, name="Old")
        self._task("Buried", date(2026, 8, 10), project=archived)
        archived.archived_at = timezone.now()
        archived.save(update_fields=["archived_at"])
        self.assertEqual(self._titles(), [])

    def test_only_the_projects_the_caller_can_open_are_read(self):
        elsewhere = create_project(self.outsider, name="Elsewhere")
        create_task(
            elsewhere, self.outsider, title="Private", due_date=date(2026, 8, 10)
        )
        self._task("Mine", date(2026, 8, 12))
        self.assertEqual(self._titles(), ["Mine"])
        self.assertEqual(self._titles(user=self.outsider), ["Private"])

    def test_earliest_due_first(self):
        self._task("Later", date(2026, 8, 20))
        self._task("Sooner", date(2026, 8, 5))
        self.assertEqual(self._titles(), ["Sooner", "Later"])

    def test_a_day_that_cannot_be_read_names_its_filter(self):
        for parameter in ("due_after", "due_before"):
            for value in ("soon", "2026-13-01", "2026-02-30"):
                with self.subTest(parameter=parameter, value=value):
                    response = self._get(**{parameter: value})
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(list(response.data), ["due"])

    def test_anonymous_request_is_rejected(self):
        response = self.client.get(URL, {"due_after": "2026-08-01"})
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
