from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.projects.models import Task
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task
from workspace.projects.today import TASKS_LIMIT, is_urgent_task, tasks_for_today

User = get_user_model()

TOKYO = ZoneInfo("Asia/Tokyo")
# 20:00 UTC on Nov 21 is already Nov 22 in Tokyo.
NOW = datetime(2026, 11, 21, 20, 0, tzinfo=UTC)
TOKYO_TODAY = date(2026, 11, 22)


class TasksForTodayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="worker", password="pw")
        self.project = create_project(self.user, name="Website")

    def _task(self, title, assignee=None, **fields):
        return create_task(
            self.project,
            self.user,
            title=title,
            assignees=[assignee or self.user],
            **fields,
        )

    def _titles(self):
        return [task.title for task in tasks_for_today(self.user, NOW, TOKYO).items]

    def test_overdue_due_today_and_urgent_tasks_are_listed(self):
        self._task("Late", due_date=TOKYO_TODAY - timedelta(days=1))
        self._task("Today", due_date=TOKYO_TODAY)
        self._task("Fire", priority=Task.Priority.URGENT)
        self._task("Next week", due_date=TOKYO_TODAY + timedelta(days=7))
        self._task("Someday")

        self.assertEqual(self._titles(), ["Late", "Fire", "Today"])

    def test_today_is_the_users_local_day(self):
        # Due on the Tokyo date, which UTC has not reached yet.
        task = self._task("Tokyo today", due_date=TOKYO_TODAY)

        rows = tasks_for_today(self.user, NOW, TOKYO)

        self.assertEqual(rows.items, [task])
        self.assertEqual(rows.total, 1)
        self.assertTrue(rows.items[0].due_today)
        self.assertFalse(rows.items[0].overdue)

    def test_nothing_due_lists_nothing(self):
        self._task("Next week", due_date=TOKYO_TODAY + timedelta(days=7))
        self._task("Someday")

        rows = tasks_for_today(self.user, NOW, TOKYO)

        self.assertEqual((rows.items, rows.total), ([], 0))

    def test_overdue_and_urgent_tasks_are_urgent_rows(self):
        self._task("Late", due_date=TOKYO_TODAY - timedelta(days=1))
        self._task("Today", due_date=TOKYO_TODAY)
        self._task("Fire", priority=Task.Priority.URGENT)

        urgent = {
            task.title: is_urgent_task(task)
            for task in tasks_for_today(self.user, NOW, TOKYO).items
        }

        self.assertEqual(urgent, {"Late": True, "Today": False, "Fire": True})

    def test_tasks_assigned_to_someone_else_are_not_listed(self):
        other = User.objects.create_user(username="other", password="pw")
        self._task("Theirs", assignee=other, due_date=TOKYO_TODAY)

        self.assertEqual(self._titles(), [])

    def test_an_urgent_task_without_a_due_date_survives_the_cap(self):
        for i in range(TASKS_LIMIT):
            self._task(f"Today {i}", due_date=TOKYO_TODAY)
        self._task("Fire", priority=Task.Priority.URGENT)

        self.assertIn("Fire", self._titles())

    def test_the_list_is_capped(self):
        for i in range(TASKS_LIMIT + 2):
            self._task(f"Task {i}", due_date=TOKYO_TODAY)

        rows = tasks_for_today(self.user, NOW, TOKYO)

        self.assertEqual(len(rows.items), TASKS_LIMIT)
        self.assertEqual(rows.total, TASKS_LIMIT + 2)
