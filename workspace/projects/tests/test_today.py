from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.projects.models import Task
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task
from workspace.projects.today import TASKS_LIMIT, tasks_for_today

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
        return [task.title for task in tasks_for_today(self.user, NOW, TOKYO)]

    def test_overdue_due_today_and_urgent_tasks_are_listed(self):
        self._task("Late", due_date=TOKYO_TODAY - timedelta(days=1))
        self._task("Today", due_date=TOKYO_TODAY)
        self._task("Fire", priority=Task.Priority.URGENT)
        self._task("Next week", due_date=TOKYO_TODAY + timedelta(days=7))
        self._task("Someday")

        self.assertEqual(self._titles(), ["Late", "Today", "Fire"])

    def test_today_is_the_users_local_day(self):
        # Due on the Tokyo date, which UTC has not reached yet.
        task = self._task("Tokyo today", due_date=TOKYO_TODAY)

        rows = tasks_for_today(self.user, NOW, TOKYO)

        self.assertEqual(rows, [task])
        self.assertTrue(rows[0].due_today)
        self.assertFalse(rows[0].overdue)
        self.assertFalse(rows[0].coming_up)

    def test_the_next_tasks_stand_in_when_nothing_is_due(self):
        self._task("Next week", due_date=TOKYO_TODAY + timedelta(days=7))
        self._task("Someday")

        rows = tasks_for_today(self.user, NOW, TOKYO)

        self.assertEqual([task.title for task in rows], ["Next week", "Someday"])
        self.assertTrue(all(task.coming_up for task in rows))

    def test_tasks_assigned_to_someone_else_are_not_listed(self):
        other = User.objects.create_user(username="other", password="pw")
        self._task("Theirs", assignee=other, due_date=TOKYO_TODAY)

        self.assertEqual(self._titles(), [])

    def test_the_list_is_capped(self):
        for i in range(TASKS_LIMIT + 2):
            self._task(f"Task {i}", due_date=TOKYO_TODAY)

        self.assertEqual(len(self._titles()), TASKS_LIMIT)
