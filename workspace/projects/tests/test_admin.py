from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.projects.models import Task, TaskEvent
from workspace.projects.services.projects import create_project

User = get_user_model()


class ProjectsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.project = create_project(cls.admin, name="Website")
        cls.task = Task.objects.create(
            project=cls.project,
            number=1,
            title="Ship the landing page",
            status=cls.project.statuses.first(),
            priority=Task.Priority.URGENT,
            created_by=cls.admin,
        )
        cls.event = TaskEvent.objects.create(
            project=cls.project,
            task=cls.task,
            task_title=cls.task.title,
            type=TaskEvent.Type.CREATED,
            actor=cls.admin,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_change_lists_render(self):
        for url_name in (
            "admin:projects_project_changelist",
            "admin:projects_projectmember_changelist",
            "admin:projects_taskstatus_changelist",
            "admin:projects_label_changelist",
            "admin:projects_task_changelist",
            "admin:projects_taskcomment_changelist",
            "admin:projects_taskevent_changelist",
        ):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)

    def test_task_change_list_shows_reference_and_priority(self):
        response = self.client.get(reverse("admin:projects_task_changelist"))
        self.assertContains(response, self.task.reference)
        self.assertContains(response, "urgent")

    def test_task_events_cannot_be_added_or_edited(self):
        self.assertEqual(
            self.client.get(reverse("admin:projects_taskevent_add")).status_code, 403
        )
