import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.projects.models import Project, Sprint, TaskEvent
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.sprints import (
    ActiveSprintError,
    SprintStateError,
    SprintTargetError,
    assign_tasks_to_sprint,
    complete_sprint,
    start_sprint,
)
from workspace.projects.services.tasks import (
    change_task_status,
    create_task,
    move_tasks,
    reorder_tasks,
)

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
        self.assertIsNotNone(sprint.closed_at)
        self.assertLess(timezone.now() - sprint.closed_at, timedelta(minutes=1))

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
    def test_assign_a_selection_larger_than_the_parameter_limit(self):
        parameter_limit = 50
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a")
        selection = [task.uuid, *(uuid.uuid4() for _ in range(parameter_limit * 2))]

        with sqlite_parameter_limit(parameter_limit):
            changed = assign_tasks_to_sprint(self.scrum, sprint, selection)

        self.assertEqual([t.uuid for t in changed], [task.uuid])
        task.refresh_from_db()
        self.assertEqual(task.sprint_id, sprint.pk)

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

    def test_assign_to_active_sprint_moves_backlog_tasks_to_board(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        planned = create_task(self.scrum, self.admin, title="planned")
        doing = create_task(self.scrum, self.admin, title="doing", status=self.doing)
        doing.sprint = None
        doing.save(update_fields=["sprint"])
        assign_tasks_to_sprint(
            self.scrum, sprint, [planned.uuid, doing.uuid], actor=self.admin
        )
        planned.refresh_from_db()
        self.assertEqual(planned.sprint_id, sprint.pk)
        # Backlog tasks joining the running sprint land on the board...
        self.assertEqual(planned.status_id, self.todo.pk)
        self.assertTrue(
            TaskEvent.objects.filter(task=planned, type=TaskEvent.Type.MOVED).exists()
        )
        # ...while tasks already on the board keep their column.
        doing.refresh_from_db()
        self.assertEqual(doing.sprint_id, sprint.pk)
        self.assertEqual(doing.status_id, self.doing.pk)

    def test_assign_to_planned_sprint_keeps_status(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a")
        assign_tasks_to_sprint(self.scrum, sprint, [task.uuid], actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status_id, self.backlog.pk)

    def test_assign_to_closed_sprint_raises(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.CLOSED)
        task = create_task(self.scrum, self.admin, title="a")
        with self.assertRaises(SprintTargetError):
            assign_tasks_to_sprint(self.scrum, sprint, [task.uuid])

    def test_assign_refuses_a_sprint_closed_after_it_was_read(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        read_before_the_close = Sprint.objects.get(pk=sprint.pk)
        complete_sprint(sprint)
        task = create_task(self.scrum, self.admin, title="a")
        with self.assertRaises(SprintTargetError):
            assign_tasks_to_sprint(self.scrum, read_before_the_close, [task.uuid])
        task.refresh_from_db()
        self.assertIsNone(task.sprint_id)

    def test_assign_follows_a_start_landed_after_the_sprint_was_read(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        read_before_the_start = Sprint.objects.get(pk=sprint.pk)
        start_sprint(sprint)
        task = create_task(self.scrum, self.admin, title="a")
        assign_tasks_to_sprint(self.scrum, read_before_the_start, [task.uuid])
        task.refresh_from_db()
        self.assertEqual(task.sprint_id, sprint.pk)
        self.assertEqual(task.status_id, self.todo.pk)

    def test_assign_refuses_a_sprint_deleted_after_it_was_read(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        read_before_the_delete = Sprint.objects.get(pk=sprint.pk)
        sprint.delete()
        task = create_task(self.scrum, self.admin, title="a")
        with self.assertRaises(SprintTargetError):
            assign_tasks_to_sprint(self.scrum, read_before_the_delete, [task.uuid])
        task.refresh_from_db()
        self.assertIsNone(task.sprint_id)

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

    def test_task_planned_for_a_later_sprint_joins_the_running_one_on_the_board(
        self,
    ):
        running = self.scrum.sprints.create(name="Sprint 1")
        start_sprint(running, actor=self.admin)
        later = self.scrum.sprints.create(name="Sprint 2")
        moves = {
            "send to board": lambda task: move_tasks(
                self.scrum, self.todo, [task.uuid], actor=self.admin
            ),
            "status change": lambda task: change_task_status(
                task, self.todo, actor=self.admin
            ),
            "drop on a column": lambda task: reorder_tasks(
                self.scrum, self.todo, [task.uuid], actor=self.admin
            ),
        }
        for name, move in moves.items():
            with self.subTest(name):
                task = create_task(self.scrum, self.admin, title=name, sprint=later)

                move(task)

                task.refresh_from_db()
                # The board only shows the running sprint's tasks.
                self.assertEqual(task.sprint, running)
                event = TaskEvent.objects.filter(
                    task=task, type=TaskEvent.Type.SPRINT
                ).latest("created_at")
                self.assertEqual((event.from_ref, event.to_ref), (later.pk, running.pk))

    def test_task_of_a_closed_sprint_keeps_it_between_board_columns(self):
        closed = self.scrum.sprints.create(name="Sprint 1")
        start_sprint(closed, actor=self.admin)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=closed, status=self.done
        )
        complete_sprint(closed, actor=self.admin)
        running = self.scrum.sprints.create(name="Sprint 2")
        start_sprint(running, actor=self.admin)

        change_task_status(task, self.doing, actor=self.admin)

        task.refresh_from_db()
        self.assertEqual(task.sprint, closed)


class SprintEventIdentityTests(SprintServiceTestCase):
    """Every SPRINT writer stores the sprint's identity next to its name."""

    def _sprint_events(self, task):
        return list(
            TaskEvent.objects.filter(task=task, type=TaskEvent.Type.SPRINT).order_by(
                "created_at"
            )
        )

    def test_born_in_sprint_and_assignment_carry_refs(self):
        first = self.scrum.sprints.create(name="Sprint 1")
        second = self.scrum.sprints.create(name="Sprint 2")
        task = create_task(self.scrum, self.admin, title="a", sprint=first)
        assign_tasks_to_sprint(self.scrum, second, [task.uuid], actor=self.admin)
        assign_tasks_to_sprint(self.scrum, None, [task.uuid], actor=self.admin)
        refs = [(e.from_ref, e.to_ref) for e in self._sprint_events(task)]
        self.assertEqual(
            refs, [(None, first.pk), (first.pk, second.pk), (second.pk, None)]
        )

    def test_carry_over_at_close_and_joining_the_running_sprint_carry_refs(self):
        first = self.scrum.sprints.create(name="Sprint 1")
        second = self.scrum.sprints.create(name="Sprint 2")
        start_sprint(first, actor=self.admin)
        carried = create_task(
            self.scrum, self.admin, title="slow", sprint=first, status=self.todo
        )
        joined = create_task(self.scrum, self.admin, title="late")
        move_tasks(self.scrum, self.todo, [joined.uuid], actor=self.admin)
        complete_sprint(first, move_to=second, actor=self.admin)
        self.assertEqual(
            [(e.from_ref, e.to_ref) for e in self._sprint_events(carried)],
            [(None, first.pk), (first.pk, second.pk)],
        )
        self.assertEqual(self._sprint_events(joined)[0].to_ref, first.pk)
