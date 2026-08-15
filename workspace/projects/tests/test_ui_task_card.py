import datetime
import uuid as uuid_module

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Label
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin


class TaskCardViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.label = Label.objects.create(project=self.project, name="bug")
        self.task = create_task(
            self.project,
            self.admin,
            title="Ship the hover card",
            status=self.todo,
            priority="urgent",
            due_date=timezone.localdate() + datetime.timedelta(days=3),
            assignees=[self.member],
            labels=[self.label],
        )
        self.url = f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/card"

    def test_member_gets_card(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "projects/ui/partials/_task_popover_card.html")
        self.assertContains(resp, "Ship the hover card")
        self.assertContains(resp, f"{self.project.key}-{self.task.number}")
        self.assertContains(resp, "To do")
        self.assertContains(resp, "Urgent")
        self.assertContains(resp, "bug")

    def test_card_links_to_the_task_on_its_board(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(
            resp, f"/projects/{self.project.uuid}/board?task={self.task.uuid}"
        )

    def test_card_shows_five_avatars_and_counts_the_rest(self):
        extras = [
            get_user_model().objects.create_user(
                username=f"crowd{i}", email=f"crowd{i}@test.com", password="pass123"
            )
            for i in range(6)
        ]
        for user in extras:
            add_member(self.project, user)
        self.task.assignees.set([self.member, *extras])

        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        html = resp.content.decode()

        # The card caps the avatar row at five and folds the remainder into a
        # "+N" chip; N counts every assignee, not just the ones rendered.
        self.assertEqual(html.count("<user-avatar "), 5)
        self.assertContains(resp, "+2")

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_task_from_another_project_gets_404(self):
        other = create_project(self.admin, name="Other")
        stranger = create_task(
            other,
            self.admin,
            title="Elsewhere",
            status=other.statuses.get(name="To do"),
        )
        self.client.force_login(self.member)
        url = f"/projects/{self.project.uuid}/tasks/{stranger.uuid}/card"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unknown_task_gets_404(self):
        self.client.force_login(self.member)
        url = f"/projects/{self.project.uuid}/tasks/{uuid_module.uuid4()}/card"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])
