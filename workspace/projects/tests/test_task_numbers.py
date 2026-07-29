from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)
from workspace.projects.services.tasks import create_task, delete_task
from workspace.projects.tests.base import ProjectTestMixin

User = get_user_model()


class TaskNumberTests(ProjectTestMixin, TestCase):
    def test_numbers_are_sequential_per_project(self):
        t1 = create_task(self.project, self.admin, title="a")
        t2 = create_task(self.project, self.admin, title="b")
        self.assertEqual((t1.number, t2.number), (1, 2))

    def test_numbers_are_scoped_to_the_project(self):
        other = create_project(self.admin, name="Other Board")
        t_here = create_task(self.project, self.admin, title="a")
        t_there = create_task(other, self.admin, title="b")
        self.assertEqual(t_here.number, 1)
        self.assertEqual(t_there.number, 1)

    def test_deleted_numbers_are_never_reused(self):
        t1 = create_task(self.project, self.admin, title="a")
        delete_task(t1, actor=self.admin)
        t2 = create_task(self.project, self.admin, title="b")
        self.assertEqual(t2.number, 2)

    def test_reference_combines_key_and_number(self):
        task = create_task(self.project, self.admin, title="a")
        self.assertEqual(task.reference, f"{self.project.key}-1")

    def test_created_event_snapshots_the_number(self):
        task = create_task(self.project, self.admin, title="a")
        event = task.events.get()
        self.assertEqual(event.task_number, task.number)

    def test_delete_event_keeps_the_number(self):
        task = create_task(self.project, self.admin, title="a")
        number = task.number
        delete_task(task, actor=self.admin)
        event = self.project.task_events.filter(type="deleted").get()
        self.assertIsNone(event.task_id)
        self.assertEqual(event.task_number, number)


class ProjectKeyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="keyuser", email="key@test.com", password="pass123"
        )

    def test_key_generated_from_name(self):
        project = create_project(self.user, name="Website Redesign")
        self.assertEqual(project.key, "WR")

    def test_key_collision_gets_suffix(self):
        create_project(self.user, name="Website Redesign")
        second = create_project(self.user, name="Web Ring")
        self.assertEqual(second.key, "WR2")

    def test_personal_project_gets_a_key(self):
        project = get_or_create_personal_project(self.user)
        self.assertEqual(project.key, "PERS")
