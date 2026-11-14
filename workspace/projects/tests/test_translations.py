"""The projects module speaks the reader's language: notifications in the
recipient's, default columns in the creator's, choice labels in the viewer's."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone, translation

from workspace.notifications.models import Notification
from workspace.projects.models import Task
from workspace.projects.services.assignments import (
    notify_assigned,
    notify_bulk_assigned,
)
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)
from workspace.projects.services.tasks import create_task
from workspace.projects.tasks import notify_due_tasks
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin

User = get_user_model()


class AssignmentNotificationLanguageTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def _task(self, title="Ship it"):
        return create_task(self.project, self.admin, title=title)

    def _title(self, user):
        return Notification.objects.get(recipient=user).title

    def test_written_in_the_assignees_language(self):
        set_setting(self.member, "core", "language", "fr")

        notify_assigned(self._task(), self.admin, [self.member])

        self.assertEqual(
            self._title(self.member), "admin1 vous a assigné «\u00a0Ship it\u00a0»"
        )

    def test_stays_english_by_default(self):
        notify_assigned(self._task(), self.admin, [self.member])

        self.assertEqual(self._title(self.member), 'admin1 assigned you to "Ship it"')

    def test_each_assignee_reads_their_own_language(self):
        set_setting(self.member, "core", "language", "fr")
        other = User.objects.create_user(username="other1", password="pass123")
        add_member(self.project, other)

        notify_assigned(self._task(), self.admin, [self.member, other])

        self.assertEqual(
            self._title(self.member), "admin1 vous a assigné «\u00a0Ship it\u00a0»"
        )
        self.assertEqual(self._title(other), 'admin1 assigned you to "Ship it"')

    def test_not_the_actors_language(self):
        set_setting(self.admin, "core", "language", "fr")

        with translation.override("fr"):
            notify_assigned(self._task(), self.admin, [self.member])

        self.assertEqual(self._title(self.member), 'admin1 assigned you to "Ship it"')

    def test_bulk_assignment_counts_in_french(self):
        set_setting(self.member, "core", "language", "fr")
        tasks = [self._task(title=f"Task {i}") for i in range(3)]

        notify_bulk_assigned(self.project, self.admin, {self.member: tasks})

        self.assertEqual(
            self._title(self.member), "admin1 vous a assigné 3 tâches dans Website"
        )

    def test_bulk_assignment_counts_in_english_by_default(self):
        tasks = [self._task(title=f"Task {i}") for i in range(3)]

        notify_bulk_assigned(self.project, self.admin, {self.member: tasks})

        self.assertEqual(
            self._title(self.member), "admin1 assigned you to 3 tasks in Website"
        )


class DueReminderLanguageTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")

    def tearDown(self):
        cache.clear()

    def _remind(self, due_days):
        task = create_task(
            self.project,
            self.admin,
            title="Ship it",
            status=self.todo,
            due_date=timezone.localdate() + timedelta(days=due_days),
            assignees=[self.member],
        )
        Notification.objects.all().delete()
        frozen = timezone.now().replace(hour=12, minute=15)
        with mock.patch("django.utils.timezone.now", return_value=frozen):
            notify_due_tasks()
        return Notification.objects.get(task=task, recipient=self.member)

    def test_due_today_in_the_recipients_language(self):
        set_setting(self.member, "core", "language", "fr")

        self.assertEqual(self._remind(0).body, "Échéance aujourd’hui · Website")

    def test_due_today_stays_english_by_default(self):
        self.assertEqual(self._remind(0).body, "Due today · Website")

    def test_overdue_names_the_date_in_the_recipients_language(self):
        set_setting(self.member, "core", "language", "fr")

        body = self._remind(-3).body

        self.assertTrue(body.startswith("En retard depuis le "), body)
        self.assertTrue(body.endswith(" · Website"), body)


class DefaultStatusLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")

    def tearDown(self):
        cache.clear()

    def _names(self, project):
        return list(
            project.statuses.order_by("position").values_list("name", flat=True)
        )

    def test_columns_are_created_in_the_creators_language(self):
        set_setting(self.user, "core", "language", "fr")

        project = create_project(self.user, name="Site")

        self.assertEqual(
            self._names(project), ["Backlog", "À faire", "En cours", "Terminé"]
        )

    def test_columns_follow_the_creator_not_the_active_language(self):
        with translation.override("fr"):
            project = create_project(self.user, name="Site")

        self.assertEqual(
            self._names(project), ["Backlog", "To do", "In progress", "Done"]
        )

    def test_personal_project_is_named_in_the_owners_language(self):
        set_setting(self.user, "core", "language", "fr")

        self.assertEqual(get_or_create_personal_project(self.user).name, "Personnel")


class ChoiceLabelLanguageTests(TestCase):
    def test_priority_reads_as_a_feminine_adjective_in_french(self):
        task = Task(priority=Task.Priority.HIGH)

        with translation.override("fr"):
            self.assertEqual(task.get_priority_display(), "Haute")
        with translation.override("en"):
            self.assertEqual(task.get_priority_display(), "High")


class ProjectPageLanguageTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.board_url = f"/projects/{self.project.uuid}/board"

    def tearDown(self):
        cache.clear()

    def test_board_renders_in_french(self):
        set_setting(self.admin, "core", "language", "fr")
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rechercher…")
        self.assertContains(response, "Vue d’ensemble")
        self.assertNotContains(response, "Search tasks")

    def test_board_stays_english_by_default(self):
        response = self.client.get(self.board_url)
        self.assertContains(response, "Search tasks")
        self.assertNotContains(response, "Rechercher…")


class ProjectApiLanguageTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = f"/api/v1/projects/{self.project.uuid}/labels"
        self.client.post(self.url, {"name": "bug"}, content_type="application/json")

    def tearDown(self):
        cache.clear()

    def _duplicate(self):
        response = self.client.post(
            self.url, {"name": "bug"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        return response.json()["name"]

    def test_validation_error_is_translated(self):
        set_setting(self.admin, "core", "language", "fr")
        self.assertEqual(
            self._duplicate(), ["Une étiquette de ce nom existe déjà dans ce projet."]
        )

    def test_validation_error_defaults_to_english(self):
        self.assertEqual(
            self._duplicate(),
            ["A label with this name already exists in this project."],
        )
