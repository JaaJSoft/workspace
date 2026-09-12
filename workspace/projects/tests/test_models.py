from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from workspace.projects.models import (
    Label,
    Milestone,
    Project,
    Task,
    TaskEvent,
    TaskStatus,
)
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

User = get_user_model()


class ProjectModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )

    def test_one_personal_project_per_user(self):
        Project.objects.create(
            name="Personal", key="P1", type=Project.Type.PERSONAL, created_by=self.user
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Project.objects.create(
                    name="Personal 2",
                    key="P2",
                    type=Project.Type.PERSONAL,
                    created_by=self.user,
                )

    def test_multiple_kanban_projects_allowed(self):
        Project.objects.create(name="A", key="A", created_by=self.user)
        Project.objects.create(name="B", key="B", created_by=self.user)
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
        Task.objects.create(
            project=self.project, number=1, title="t", status=self.status
        )
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
            project=self.project,
            number=1,
            title="second",
            status=self.status,
            position=1,
        )
        t2 = Task.objects.create(
            project=self.project,
            number=2,
            title="first",
            status=self.status,
            position=0,
        )
        self.assertEqual(list(Task.objects.all()), [t2, t1])

    def test_epic_unique_per_project(self):
        from workspace.projects.models import Epic

        Epic.objects.create(project=self.project, name="Launch")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Epic.objects.create(project=self.project, name="Launch")

    def test_clean_rejects_epic_from_another_project(self):
        from django.core.exceptions import ValidationError

        from workspace.projects.models import Epic

        other = Project.objects.create(name="B", key="B2", created_by=self.user)
        foreign = Epic.objects.create(project=other, name="Elsewhere")
        task = Task(
            project=self.project,
            number=1,
            title="t",
            status=self.status,
            epic=foreign,
        )
        with self.assertRaises(ValidationError) as caught:
            task.full_clean()
        self.assertIn("epic", caught.exception.message_dict)

    def test_label_unique_per_project(self):
        Label.objects.create(project=self.project, name="bug", color="#ff0000")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Label.objects.create(project=self.project, name="bug", color="#00ff00")


class MilestoneModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="m", password="x")
        self.project = create_project(self.user, name="P")

    def test_milestone_unique_name_per_project(self):
        Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        with self.assertRaises(IntegrityError):
            Milestone.objects.create(
                project=self.project, name="Beta", target_date=date(2026, 11, 1)
            )

    def test_milestones_order_by_target_date_then_name(self):
        later = Milestone.objects.create(
            project=self.project, name="Zed", target_date=date(2026, 12, 1)
        )
        early_b = Milestone.objects.create(
            project=self.project, name="B", target_date=date(2026, 10, 1)
        )
        early_a = Milestone.objects.create(
            project=self.project, name="A", target_date=date(2026, 10, 1)
        )
        self.assertEqual(list(self.project.milestones.all()), [early_a, early_b, later])

    def test_is_closed_setter_stamps_once_and_clears(self):
        milestone = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        self.assertFalse(milestone.is_closed)
        milestone.is_closed = True
        first = milestone.closed_at
        self.assertIsNotNone(first)
        milestone.is_closed = True
        self.assertEqual(milestone.closed_at, first)
        milestone.is_closed = False
        self.assertIsNone(milestone.closed_at)

    def test_task_clean_rejects_milestone_from_another_project(self):
        other = create_project(self.user, name="Other")
        foreign = Milestone.objects.create(
            project=other, name="Elsewhere", target_date=date(2026, 10, 1)
        )
        task = create_task(self.project, self.user, title="t")
        task.milestone = foreign
        with self.assertRaises(ValidationError):
            task.clean()

    def test_deleting_milestone_nulls_task_fk(self):
        milestone = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        task = create_task(self.project, self.user, title="t")
        task.milestone = milestone
        task.save(update_fields=["milestone"])
        milestone.delete()
        task.refresh_from_db()
        self.assertIsNone(task.milestone)

    def test_task_start_date_defaults_to_none(self):
        task = create_task(self.project, self.user, title="t")
        self.assertIsNone(task.start_date)

    def test_milestone_event_type_metadata(self):
        self.assertEqual(TaskEvent.Type.MILESTONE, "milestone")
        self.assertEqual(TaskEvent._ICONS[TaskEvent.Type.MILESTONE], "milestone")
        self.assertEqual(
            TaskEvent._LABELS[TaskEvent.Type.MILESTONE], "Milestone changed"
        )
