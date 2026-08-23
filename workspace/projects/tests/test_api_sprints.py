from datetime import date

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project, Sprint, TaskEvent
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ScrumProjectMixin(ProjectTestMixin):
    """ProjectTestMixin plus a scrum project sharing the same members."""

    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        from workspace.projects.services.members import add_member

        add_member(self.scrum, self.member)
        self.backlog = self.scrum.statuses.get(name="Backlog")
        self.todo = self.scrum.statuses.get(name="To do")
        self.doing = self.scrum.statuses.get(name="In progress")
        self.done = self.scrum.statuses.get(name="Done")
        self.sprints_url = f"/api/v1/projects/{self.scrum.uuid}/sprints"


class SprintCrudApiTests(ScrumProjectMixin, APITestCase):
    def test_admin_creates_sprint(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.sprints_url,
            {"name": "Sprint 1", "goal": "Ship it", "start_date": "2026-09-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sprint = self.scrum.sprints.get()
        self.assertEqual(sprint.name, "Sprint 1")
        self.assertEqual(sprint.state, Sprint.State.PLANNED)
        self.assertEqual(sprint.start_date, date(2026, 9, 1))

    def test_member_cannot_create_sprint(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.sprints_url, {"name": "Sprint 1"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.sprints_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_lists_sprints_with_rollup(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        create_task(self.scrum, self.admin, title="a", sprint=sprint)
        create_task(self.scrum, self.admin, title="b", sprint=sprint, status=self.done)
        self.client.force_authenticate(self.member)
        response = self.client.get(self.sprints_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["name"], "Sprint 1")
        self.assertEqual(response.data[0]["task_count"], 2)
        self.assertEqual(response.data[0]["done_task_count"], 1)

    def test_duplicate_name_is_400(self):
        self.scrum.sprints.create(name="Sprint 1")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.sprints_url, {"name": "Sprint 1"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_end_before_start_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.sprints_url,
            {
                "name": "Sprint 1",
                "start_date": "2026-09-15",
                "end_date": "2026-09-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_state_is_not_writable(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"{self.sprints_url}/{sprint.uuid}", {"state": "active"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.PLANNED)

    def test_delete_planned_sprint_keeps_tasks(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        task = create_task(self.scrum, self.admin, title="a", sprint=sprint)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"{self.sprints_url}/{sprint.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        task.refresh_from_db()
        self.assertIsNone(task.sprint)

    def test_delete_active_sprint_is_400(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"{self.sprints_url}/{sprint.uuid}")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SprintLifecycleApiTests(ScrumProjectMixin, APITestCase):
    def test_start_moves_backlog_tasks_to_first_active_column(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        planned = create_task(self.scrum, self.admin, title="a", sprint=sprint)
        unplanned = create_task(self.scrum, self.admin, title="b")
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"{self.sprints_url}/{sprint.uuid}/start")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["state"], "active")
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.ACTIVE)
        self.assertIsNotNone(sprint.start_date)
        planned.refresh_from_db()
        self.assertEqual(planned.status_id, self.todo.pk)
        unplanned.refresh_from_db()
        self.assertEqual(unplanned.status_id, self.backlog.pk)

    def test_start_requires_admin(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        self.client.force_authenticate(self.member)
        response = self.client.post(f"{self.sprints_url}/{sprint.uuid}/start")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_second_active_sprint_is_400(self):
        self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        sprint = self.scrum.sprints.create(name="Sprint 2")
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"{self.sprints_url}/{sprint.uuid}/start")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_closed_sprint_is_400(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.CLOSED)
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"{self.sprints_url}/{sprint.uuid}/start")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_complete_returns_unfinished_tasks_to_backlog(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        open_task = create_task(
            self.scrum, self.admin, title="open", sprint=sprint, status=self.doing
        )
        done_task = create_task(
            self.scrum, self.admin, title="done", sprint=sprint, status=self.done
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"{self.sprints_url}/{sprint.uuid}/complete", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.CLOSED)
        self.assertIsNotNone(sprint.end_date)
        open_task.refresh_from_db()
        self.assertIsNone(open_task.sprint)
        self.assertEqual(open_task.status_id, self.backlog.pk)
        done_task.refresh_from_db()
        self.assertEqual(done_task.sprint_id, sprint.pk)
        self.assertEqual(done_task.status_id, self.done.pk)
        events = TaskEvent.objects.filter(task=open_task, type=TaskEvent.Type.SPRINT)
        self.assertTrue(events.filter(from_value="Sprint 1", to_value="").exists())

    def test_complete_carries_unfinished_tasks_to_next_sprint(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        next_sprint = self.scrum.sprints.create(name="Sprint 2")
        open_task = create_task(
            self.scrum, self.admin, title="open", sprint=sprint, status=self.doing
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"{self.sprints_url}/{sprint.uuid}/complete",
            {"move_to": str(next_sprint.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        open_task.refresh_from_db()
        self.assertEqual(open_task.sprint_id, next_sprint.pk)
        # Carried-over tasks resume where they stopped: the status stays.
        self.assertEqual(open_task.status_id, self.doing.pk)

    def test_complete_to_closed_sprint_is_400(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        closed = self.scrum.sprints.create(name="Sprint 0", state=Sprint.State.CLOSED)
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"{self.sprints_url}/{sprint.uuid}/complete",
            {"move_to": str(closed.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_complete_planned_sprint_is_400(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"{self.sprints_url}/{sprint.uuid}/complete", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AssignSprintApiTests(ScrumProjectMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.sprint = self.scrum.sprints.create(name="Sprint 1")
        self.t1 = create_task(self.scrum, self.admin, title="t1")
        self.t2 = create_task(self.scrum, self.admin, title="t2")
        self.url = f"/api/v1/projects/{self.scrum.uuid}/tasks/assign-sprint"

    def test_member_assigns_selection_to_sprint(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "sprint": str(self.sprint.uuid),
                "tasks": [str(self.t1.uuid), str(self.t2.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated"], 2)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.sprint_id, self.sprint.pk)
        # Planning never touches the board status.
        self.assertEqual(self.t1.status_id, self.backlog.pk)
        event = TaskEvent.objects.get(task=self.t1, type=TaskEvent.Type.SPRINT)
        self.assertEqual(event.to_value, "Sprint 1")

    def test_null_sprint_clears_assignment(self):
        self.t1.sprint = self.sprint
        self.t1.save(update_fields=["sprint"])
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"sprint": None, "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.sprint)

    def test_unknown_sprint_is_400(self):
        other = create_project(self.admin, name="Elsewhere")
        foreign = other.sprints.create(name="Sprint X")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"sprint": str(foreign.uuid), "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_closed_sprint_target_is_400(self):
        self.sprint.state = Sprint.State.CLOSED
        self.sprint.save(update_fields=["state"])
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"sprint": str(self.sprint.uuid), "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ScrumBoardEntryApiTests(ScrumProjectMixin, APITestCase):
    """Tasks entering a board column of a scrum project join the running
    sprint - otherwise the sprint-filtered board would never show them."""

    def setUp(self):
        super().setUp()
        self.sprint = self.scrum.sprints.create(
            name="Sprint 1", state=Sprint.State.ACTIVE
        )

    def test_task_created_on_board_column_joins_active_sprint(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.scrum.uuid}/tasks",
            {"title": "On board", "status": str(self.todo.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sprint"], self.sprint.uuid)

    def test_task_created_in_backlog_stays_unplanned(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.scrum.uuid}/tasks",
            {"title": "In backlog"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["sprint"])

    def test_bulk_move_to_board_joins_active_sprint(self):
        task = create_task(self.scrum, self.admin, title="t")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.scrum.uuid}/tasks/move",
            {"status": str(self.todo.uuid), "tasks": [str(task.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertEqual(task.sprint_id, self.sprint.pk)

    def test_patch_sprint_records_event(self):
        task = create_task(self.scrum, self.admin, title="t")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"/api/v1/projects/{self.scrum.uuid}/tasks/{task.uuid}",
            {"sprint": str(self.sprint.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = TaskEvent.objects.get(task=task, type=TaskEvent.Type.SPRINT)
        self.assertEqual(event.to_value, "Sprint 1")

    def test_patch_to_closed_sprint_is_400(self):
        closed = self.scrum.sprints.create(name="Sprint 0", state=Sprint.State.CLOSED)
        task = create_task(self.scrum, self.admin, title="t")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"/api/v1/projects/{self.scrum.uuid}/tasks/{task.uuid}",
            {"sprint": str(closed.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProjectTypeApiTests(ScrumProjectMixin, APITestCase):
    def test_create_scrum_project(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/projects",
            {"name": "Iterations", "type": "scrum"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["type"], "scrum")
        project = Project.objects.get(uuid=response.data["uuid"])
        self.assertEqual(project.type, Project.Type.SCRUM)
        # Scrum projects reuse the default columns.
        self.assertEqual(project.statuses.count(), 4)

    def test_create_defaults_to_kanban(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/projects", {"name": "Plain"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["type"], "kanban")

    def test_create_personal_type_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/projects",
            {"name": "Mine", "type": "personal"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_type_cannot_change(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"type": "scrum"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SprintActionsApiTests(ScrumProjectMixin, APITestCase):
    def _actions(self, user, project):
        self.client.force_authenticate(user)
        response = self.client.post(
            "/api/v1/projects/actions",
            {"uuids": [str(project.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [a["id"] for a in response.data[str(project.uuid)]]

    def test_manage_sprints_for_scrum_admin_only(self):
        self.assertIn("manage_sprints", self._actions(self.admin, self.scrum))
        self.assertNotIn("manage_sprints", self._actions(self.member, self.scrum))
        self.assertNotIn("manage_sprints", self._actions(self.admin, self.project))
