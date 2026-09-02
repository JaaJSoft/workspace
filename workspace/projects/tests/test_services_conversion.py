from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, Sprint, TaskEvent
from workspace.projects.services.conversion import (
    ProjectTypeError,
    convert_project_type,
)
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ConversionTestCase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.doing = self.project.statuses.get(name="In progress")
        self.done = self.project.statuses.get(name="Done")

    def scrum_project(self, name="Rocket"):
        return create_project(self.admin, name=name, project_type=Project.Type.SCRUM)


class ToScrumTests(ConversionTestCase):
    def test_board_tasks_join_a_new_running_sprint(self):
        on_board = create_task(
            self.project, self.admin, title="doing", status=self.doing
        )
        completed = create_task(
            self.project, self.admin, title="done", status=self.done
        )
        pending = create_task(
            self.project, self.admin, title="later", status=self.backlog
        )

        convert_project_type(self.project, Project.Type.SCRUM, actor=self.admin)

        self.project.refresh_from_db()
        self.assertEqual(self.project.type, Project.Type.SCRUM)
        sprint = self.project.sprints.get()
        self.assertEqual(sprint.name, "Sprint 1")
        self.assertEqual(sprint.state, Sprint.State.ACTIVE)
        self.assertEqual(sprint.start_date, timezone.localdate())
        for task in (on_board, completed, pending):
            task.refresh_from_db()
        self.assertEqual(on_board.sprint_id, sprint.pk)
        self.assertEqual(completed.sprint_id, sprint.pk)
        self.assertIsNone(pending.sprint_id)

    def test_board_tasks_keep_their_column(self):
        task = create_task(self.project, self.admin, title="doing", status=self.doing)
        convert_project_type(self.project, Project.Type.SCRUM, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status_id, self.doing.pk)

    def test_records_a_sprint_event_per_task(self):
        task = create_task(self.project, self.admin, title="doing", status=self.doing)
        convert_project_type(self.project, Project.Type.SCRUM, actor=self.admin)
        event = TaskEvent.objects.get(task=task, type=TaskEvent.Type.SPRINT)
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.to_value, "Sprint 1")

    def test_empty_project_still_opens_a_sprint(self):
        convert_project_type(self.project, Project.Type.SCRUM)
        self.assertEqual(self.project.sprints.get().state, Sprint.State.ACTIVE)

    def test_round_trip_reopens_unfinished_work_without_rewriting_history(self):
        project = self.scrum_project()
        doing = project.statuses.get(name="In progress")
        done = project.statuses.get(name="Done")
        sprint = project.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        unfinished = create_task(
            project, self.admin, title="wip", status=doing, sprint=sprint
        )
        shipped = create_task(
            project, self.admin, title="shipped", status=done, sprint=sprint
        )

        convert_project_type(project, Project.Type.KANBAN, actor=self.admin)
        convert_project_type(project, Project.Type.SCRUM, actor=self.admin)

        new_sprint = project.sprints.get(state=Sprint.State.ACTIVE)
        self.assertEqual(new_sprint.name, "Sprint 2")
        unfinished.refresh_from_db()
        shipped.refresh_from_db()
        self.assertEqual(unfinished.sprint_id, new_sprint.pk)
        self.assertEqual(shipped.sprint_id, sprint.pk)

    def test_reuses_a_sprint_that_is_already_running(self):
        sprint = self.project.sprints.create(name="Leftover", state=Sprint.State.ACTIVE)
        convert_project_type(self.project, Project.Type.SCRUM)
        self.assertEqual(self.project.sprints.count(), 1)
        self.assertEqual(self.project.sprints.get().pk, sprint.pk)


class ToKanbanTests(ConversionTestCase):
    def test_closes_the_running_sprint_without_moving_its_tasks(self):
        project = self.scrum_project()
        doing = project.statuses.get(name="In progress")
        sprint = project.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        task = create_task(
            project, self.admin, title="wip", status=doing, sprint=sprint
        )

        convert_project_type(project, Project.Type.KANBAN, actor=self.admin)

        project.refresh_from_db()
        sprint.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(project.type, Project.Type.KANBAN)
        self.assertEqual(sprint.state, Sprint.State.CLOSED)
        self.assertEqual(sprint.end_date, timezone.localdate())
        self.assertIsNotNone(sprint.closed_at)
        self.assertEqual(task.status_id, doing.pk)
        self.assertEqual(task.sprint_id, sprint.pk)

    def test_keeps_an_explicit_end_date(self):
        project = self.scrum_project()
        end = timezone.localdate().replace(day=1)
        sprint = project.sprints.create(
            name="Sprint 1", state=Sprint.State.ACTIVE, end_date=end
        )
        convert_project_type(project, Project.Type.KANBAN)
        sprint.refresh_from_db()
        self.assertEqual(sprint.end_date, end)

    def test_drops_planned_sprints_and_unplans_their_tasks(self):
        project = self.scrum_project()
        backlog = project.statuses.get(name="Backlog")
        planned = project.sprints.create(name="Sprint 2")
        task = create_task(
            project, self.admin, title="later", status=backlog, sprint=planned
        )

        convert_project_type(project, Project.Type.KANBAN)

        self.assertFalse(project.sprints.filter(pk=planned.pk).exists())
        task.refresh_from_db()
        self.assertIsNone(task.sprint_id)

    def test_keeps_closed_sprints_as_history(self):
        project = self.scrum_project()
        closed = project.sprints.create(name="Sprint 0", state=Sprint.State.CLOSED)
        convert_project_type(project, Project.Type.KANBAN)
        closed.refresh_from_db()
        self.assertEqual(closed.state, Sprint.State.CLOSED)


class ConversionGuardTests(ConversionTestCase):
    def test_same_type_is_a_no_op(self):
        create_task(self.project, self.admin, title="doing", status=self.doing)
        convert_project_type(self.project, Project.Type.KANBAN)
        self.assertEqual(self.project.sprints.count(), 0)

    def test_personal_projects_cannot_be_converted(self):
        personal = get_or_create_personal_project(self.member)
        with self.assertRaises(ProjectTypeError):
            convert_project_type(personal, Project.Type.SCRUM)
        personal.refresh_from_db()
        self.assertEqual(personal.type, Project.Type.PERSONAL)

    def test_projects_cannot_become_personal(self):
        with self.assertRaises(ProjectTypeError):
            convert_project_type(self.project, Project.Type.PERSONAL)

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ProjectTypeError):
            convert_project_type(self.project, "waterfall")
