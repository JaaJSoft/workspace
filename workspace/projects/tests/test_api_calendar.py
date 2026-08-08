from datetime import date

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

URL = "/api/v1/projects/tasks/calendar"


class TaskCalendarApiTests(ProjectTestMixin, APITestCase):
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

    def _get(self, start="2026-08-01", end="2026-09-01", user=None):
        self.client.force_authenticate(user or self.member)
        return self.client.get(URL, {"start": start, "end": end})

    def test_returns_tasks_due_in_range(self):
        task = self._task("Ship it", date(2026, 8, 15))
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(str(entry["uuid"]), str(task.uuid))
        self.assertEqual(entry["title"], "Ship it")
        self.assertEqual(str(entry["due_date"]), "2026-08-15")
        self.assertEqual(entry["reference"], f"{self.project.key}-{task.number}")
        self.assertEqual(entry["project_name"], self.project.name)
        self.assertEqual(
            entry["url"], f"/projects/{self.project.uuid}/board?task={task.uuid}"
        )

    def test_range_is_start_inclusive_end_exclusive(self):
        self._task("First day", date(2026, 8, 1))
        self._task("Last day", date(2026, 8, 31))
        self._task("Day after", date(2026, 9, 1))
        self._task("Day before", date(2026, 7, 31))
        response = self._get()
        self.assertEqual([t["title"] for t in response.data], ["First day", "Last day"])

    def test_undated_tasks_are_absent(self):
        self._task("No deadline", None)
        response = self._get()
        self.assertEqual(response.data, [])

    def test_completed_tasks_are_excluded(self):
        self._task("Already done", date(2026, 8, 10), task_status=self.done)
        response = self._get()
        self.assertEqual(response.data, [])

    def test_archived_projects_are_excluded(self):
        self._task("Shelved", date(2026, 8, 10))
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self._get()
        self.assertEqual(response.data, [])

    def test_only_accessible_projects_are_returned(self):
        other = create_project(self.outsider, name="Secret")
        self._task(
            "Not yours",
            date(2026, 8, 10),
            project=other,
            task_status=other.statuses.get(name="To do"),
        )
        mine = self._task("Mine", date(2026, 8, 12))
        response = self._get()
        self.assertEqual([str(t["uuid"]) for t in response.data], [str(mine.uuid)])

    def test_results_are_ordered_by_due_date(self):
        self._task("Later", date(2026, 8, 20))
        self._task("Sooner", date(2026, 8, 5))
        response = self._get()
        self.assertEqual([t["title"] for t in response.data], ["Sooner", "Later"])

    def test_accepts_fullcalendar_iso_datetimes(self):
        self._task("In window", date(2026, 8, 15))
        response = self._get(
            start="2026-08-01T00:00:00+02:00", end="2026-09-01T00:00:00+02:00"
        )
        self.assertEqual(len(response.data), 1)

    def test_datetime_boundary_uses_the_grid_day_not_utc(self):
        """A +02:00 midnight is still the 1st for the user looking at the grid.

        Normalizing to UTC would move the boundary to 2026-07-31T22:00Z and
        pull the previous day's tasks into the window.
        """
        self._task("Day before", date(2026, 7, 31))
        response = self._get(
            start="2026-08-01T00:00:00+02:00", end="2026-09-01T00:00:00+02:00"
        )
        self.assertEqual(response.data, [])

    def test_missing_parameters_are_rejected(self):
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.client.get(URL, {"start": "2026-08-01"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_malformed_dates_are_rejected(self):
        for start, end in [
            ("garbage", "2026-09-01"),
            ("2026-08-01", "nope"),
            ("2026-13-01", "2026-09-01"),
        ]:
            with self.subTest(start=start, end=end):
                self.assertEqual(
                    self._get(start=start, end=end).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )

    def test_inverted_and_empty_ranges_are_rejected(self):
        self.assertEqual(
            self._get(start="2026-09-01", end="2026-08-01").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self._get(start="2026-08-01", end="2026-08-01").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_oversized_range_is_rejected(self):
        self.assertEqual(
            self._get(start="2026-01-01", end="2028-01-01").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_requires_authentication(self):
        response = self.client.get(URL, {"start": "2026-08-01", "end": "2026-09-01"})
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
