from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, Sprint, TaskEvent
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.sprints import (
    ActiveSprintError,
    SprintStateError,
    SprintTargetError,
    active_sprint,
    assign_tasks_to_sprint,
    complete_sprint,
    start_sprint,
)
from workspace.projects.services.tasks import create_task, move_tasks

from .base import ProjectTestMixin


class SprintServiceTestCase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        add_member(self.scrum, self.member)
        self.backlog = self.scrum.statuses.get(name="Backlog")
        self.todo = self.scrum.statuses.get(name="To do")
        self.doing = self.scrum.statuses.get(name="In progress")
        self.done = self.scrum.statuses.get(name="Done")


class StartSprintTests(SprintServiceTestCase):
    def test_start_defaults_start_date_and_moves_backlog_tasks(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a", sprint=sprint)
        started = create_task(
            self.scrum, self.admin, title="b", sprint=sprint, status=self.doing
        )
        start_sprint(sprint, actor=self.admin)
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.ACTIVE)
        self.assertEqual(sprint.start_date, timezone.localdate())
        task.refresh_from_db()
        self.assertEqual(task.status_id, self.todo.pk)
        self.assertTrue(
            TaskEvent.objects.filter(task=task, type=TaskEvent.Type.MOVED).exists()
        )
        # A task already on the board keeps its column.
        started.refresh_from_db()
        self.assertEqual(started.status_id, self.doing.pk)

    def test_start_keeps_explicit_start_date(self):
        sprint = self.scrum.sprints.create(
            name="Sprint 1", start_date=timezone.localdate().replace(day=1)
        )
        start_sprint(sprint)
        sprint.refresh_from_db()
        self.assertEqual(sprint.start_date, timezone.localdate().replace(day=1))

    def test_start_refuses_non_planned_sprint(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.CLOSED)
        with self.assertRaises(SprintStateError):
            start_sprint(sprint)

    def test_start_refuses_second_active(self):
        self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        sprint = self.scrum.sprints.create(name="Sprint 2")
        with self.assertRaises(ActiveSprintError):
            start_sprint(sprint)


class CompleteSprintTests(SprintServiceTestCase):
    def test_complete_stamps_end_date_and_closes(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        complete_sprint(sprint)
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.CLOSED)
        self.assertEqual(sprint.end_date, timezone.localdate())

    def test_complete_appends_returned_tasks_to_backlog_tail(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        existing = create_task(self.scrum, self.admin, title="existing")
        in_sprint = create_task(
            self.scrum, self.admin, title="open", sprint=sprint, status=self.doing
        )
        complete_sprint(sprint, actor=self.admin)
        in_sprint.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(in_sprint.status_id, self.backlog.pk)
        self.assertGreater(in_sprint.position, existing.position)

    def test_complete_refuses_target_from_another_project(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        other = create_project(
            self.admin, name="Elsewhere", project_type=Project.Type.SCRUM
        )
        foreign = other.sprints.create(name="Sprint X")
        with self.assertRaises(SprintTargetError):
            complete_sprint(sprint, move_to=foreign)

    def test_complete_refuses_inactive_sprint(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        with self.assertRaises(SprintStateError):
            complete_sprint(sprint)


class AssignTasksTests(SprintServiceTestCase):
    def test_assign_skips_tasks_already_on_sprint(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a", sprint=sprint)
        other = create_task(self.scrum, self.admin, title="b")
        changed = assign_tasks_to_sprint(
            self.scrum, sprint, [task.uuid, other.uuid], actor=self.admin
        )
        self.assertEqual([t.title for t in changed], ["b"])
        # One event for the newly assigned task only, none for the no-op.
        self.assertEqual(
            TaskEvent.objects.filter(task=other, type=TaskEvent.Type.SPRINT).count(),
            1,
        )
        self.assertEqual(
            TaskEvent.objects.filter(
                task=task, type=TaskEvent.Type.SPRINT, from_value=""
            ).count(),
            1,  # the creation event, not an assignment one
        )

    def test_assign_to_closed_sprint_raises(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.CLOSED)
        task = create_task(self.scrum, self.admin, title="a")
        with self.assertRaises(SprintTargetError):
            assign_tasks_to_sprint(self.scrum, sprint, [task.uuid])

    def test_clearing_records_removal_event(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a", sprint=sprint)
        assign_tasks_to_sprint(self.scrum, None, [task.uuid], actor=self.admin)
        task.refresh_from_db()
        self.assertIsNone(task.sprint)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=task,
                type=TaskEvent.Type.SPRINT,
                from_value="Sprint 1",
                to_value="",
            ).exists()
        )


class DefaultSprintTests(SprintServiceTestCase):
    def test_kanban_projects_never_auto_assign(self):
        task = create_task(
            self.project,
            self.admin,
            title="a",
            status=self.project.statuses.get(name="To do"),
        )
        self.assertIsNone(task.sprint)

    def test_move_without_active_sprint_leaves_tasks_unplanned(self):
        task = create_task(self.scrum, self.admin, title="a")
        move_tasks(self.scrum, self.todo, [task.uuid], actor=self.admin)
        task.refresh_from_db()
        self.assertIsNone(task.sprint)

    def test_active_sprint_helper(self):
        self.assertIsNone(active_sprint(self.scrum))
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        self.assertEqual(active_sprint(self.scrum), sprint)
