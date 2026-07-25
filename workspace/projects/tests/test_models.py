from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from workspace.projects.models import Label, Project, Task, TaskStatus

User = get_user_model()


class ProjectModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )

    def test_one_personal_project_per_user(self):
        Project.objects.create(
            name="Personal", type=Project.Type.PERSONAL, created_by=self.user
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Project.objects.create(
                    name="Personal 2", type=Project.Type.PERSONAL, created_by=self.user
                )

    def test_multiple_kanban_projects_allowed(self):
        Project.objects.create(name="A", created_by=self.user)
        Project.objects.create(name="B", created_by=self.user)
        self.assertEqual(Project.objects.count(), 2)

    def test_is_archived_property(self):
        from django.utils import timezone

        project = Project.objects.create(name="A", created_by=self.user)
        self.assertFalse(project.is_archived)
        project.archived_at = timezone.now()
        self.assertTrue(project.is_archived)


class TaskStatusModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.project = Project.objects.create(name="A", created_by=self.user)
        self.status = TaskStatus.objects.create(
            project=self.project, name="To do", category=TaskStatus.Category.ACTIVE
        )

    def test_status_with_tasks_is_restricted(self):
        Task.objects.create(project=self.project, title="t", status=self.status)
        with self.assertRaises(RestrictedError):
            self.status.delete()

    def test_unique_name_per_project(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskStatus.objects.create(
                    project=self.project,
                    name="To do",
                    category=TaskStatus.Category.ACTIVE,
                )


class TaskModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.project = Project.objects.create(name="A", created_by=self.user)
        self.status = TaskStatus.objects.create(
            project=self.project, name="To do", category=TaskStatus.Category.ACTIVE
        )

    def test_ordering_by_position_then_created(self):
        t1 = Task.objects.create(
            project=self.project, title="second", status=self.status, position=1
        )
        t2 = Task.objects.create(
            project=self.project, title="first", status=self.status, position=0
        )
        self.assertEqual(list(Task.objects.all()), [t2, t1])

    def test_label_unique_per_project(self):
        Label.objects.create(project=self.project, name="bug", color="#ff0000")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Label.objects.create(project=self.project, name="bug", color="#00ff00")
