import json
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from workspace.notifications.models import Notification
from workspace.projects.ai_tools import (
    CommentOnTaskParams,
    CreateTaskParams,
    ListMyTasksParams,
    MoveTaskParams,
    ProjectsToolProvider,
    SearchTasksParams,
    UpdateTaskParams,
)
from workspace.projects.models import Project, TaskComment, TaskEvent, TaskStatus
from workspace.projects.services.tasks import create_task
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin


class ProjectsAiToolsTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = ProjectsToolProvider()

    def tearDown(self):
        cache.clear()

    def _call(self, tool, args, user):
        handler = getattr(self.provider, tool)
        return handler(args, user=user, bot=None, conversation_id=None, context={})

    def _status(self, name):
        return self.project.statuses.get(name=name)

    # -- list_projects -------------------------------------------------------

    def test_list_projects_returns_statuses_in_board_order(self):
        result = self._call("list_projects", {}, self.member)
        data = json.loads(result)
        self.assertEqual([p["name"] for p in data], ["Website"])
        self.assertEqual(
            data[0]["statuses"], ["Backlog", "To do", "In progress", "Done"]
        )

    def test_list_projects_excludes_inaccessible_and_archived(self):
        result = self._call("list_projects", {}, self.outsider)
        self.assertEqual(result, "You have no projects yet.")

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        result = self._call("list_projects", {}, self.member)
        self.assertEqual(result, "You have no projects yet.")

    # -- list_my_tasks -------------------------------------------------------

    def test_list_my_tasks_returns_assigned_open_tasks_only(self):
        mine = create_task(
            self.project, self.admin, title="Mine", assignees=[self.member]
        )
        create_task(self.project, self.admin, title="Unassigned")
        done = create_task(
            self.project,
            self.admin,
            title="Finished",
            assignees=[self.member],
            status=self._status("Done"),
        )
        result = self._call("list_my_tasks", ListMyTasksParams(), self.member)
        data = json.loads(result)
        self.assertEqual([t["title"] for t in data], ["Mine"])
        self.assertEqual(data[0]["uuid"], str(mine.uuid))
        self.assertEqual(data[0]["reference"], mine.reference)
        self.assertNotIn(str(done.uuid), result)

    def test_list_my_tasks_due_window_filter(self):
        today = timezone.localdate()
        create_task(
            self.project,
            self.admin,
            title="Due soon",
            assignees=[self.member],
            due_date=today + timedelta(days=2),
        )
        create_task(
            self.project,
            self.admin,
            title="Due later",
            assignees=[self.member],
            due_date=today + timedelta(days=30),
        )
        result = self._call(
            "list_my_tasks", ListMyTasksParams(due_within_days=7), self.member
        )
        self.assertIn("Due soon", result)
        self.assertNotIn("Due later", result)

    def test_list_my_tasks_due_window_is_read_in_the_user_timezone(self):
        # 23:30 UTC is already tomorrow in Paris, so the window a user asks
        # for is one day off unless their own date is what it is measured
        # from. Nothing activates a timezone in a Celery worker, and a tool
        # running off the main thread would not see it if anything did.
        set_setting(self.member, "core", "timezone", "Europe/Paris")
        frozen = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        local_tomorrow = date(2026, 1, 17)
        create_task(
            self.project,
            self.admin,
            title="Due the day after in Paris",
            assignees=[self.member],
            due_date=local_tomorrow,
        )

        with mock.patch("django.utils.timezone.now", return_value=frozen):
            result = self._call(
                "list_my_tasks", ListMyTasksParams(due_within_days=1), self.member
            )

        self.assertIn("Due the day after in Paris", result)

    # -- search_tasks --------------------------------------------------------

    def test_search_tasks_finds_by_keyword_across_assignees(self):
        create_task(
            self.project, self.admin, title="Fix login bug", assignees=[self.admin]
        )
        result = self._call(
            "search_tasks", SearchTasksParams(query="login"), self.member
        )
        data = json.loads(result)
        self.assertEqual([t["title"] for t in data], ["Fix login bug"])

    def test_search_tasks_access_filtered(self):
        create_task(self.project, self.admin, title="Fix login bug")
        result = self._call(
            "search_tasks", SearchTasksParams(query="login"), self.outsider
        )
        self.assertIn("No tasks found", result)

    def test_search_tasks_status_and_assignee_filters(self):
        create_task(
            self.project,
            self.admin,
            title="Deploy website",
            assignees=[self.member],
            status=self._status("In progress"),
        )
        create_task(self.project, self.admin, title="Design website")
        result = self._call(
            "search_tasks",
            SearchTasksParams(query="website", status="In progress"),
            self.member,
        )
        data = json.loads(result)
        self.assertEqual([t["title"] for t in data], ["Deploy website"])

        result = self._call(
            "search_tasks",
            SearchTasksParams(query="website", assignee="member1"),
            self.member,
        )
        data = json.loads(result)
        self.assertEqual([t["title"] for t in data], ["Deploy website"])

    def test_search_tasks_matches_reference_first(self):
        task = create_task(self.project, self.admin, title="Obscure")
        result = self._call(
            "search_tasks",
            SearchTasksParams(query=task.reference.lower()),
            self.member,
        )
        data = json.loads(result)
        self.assertEqual([t["uuid"] for t in data], [str(task.uuid)])

    def test_search_tasks_filters_apply_to_reference_matches(self):
        task = create_task(self.project, self.admin, title="Obscure")
        result = self._call(
            "search_tasks",
            SearchTasksParams(query=task.reference, status="Done"),
            self.member,
        )
        self.assertIn("No tasks found", result)

    # -- create_task ---------------------------------------------------------

    def test_create_task_in_named_project_with_assignee(self):
        result = self._call(
            "create_task",
            CreateTaskParams(
                title="Ship it",
                project="Website",
                priority="high",
                due_date="2026-09-01",
                assignee="member1",
            ),
            self.admin,
        )
        task = self.project.tasks.get(title="Ship it")
        self.assertIn(task.reference, result)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.due_date.isoformat(), "2026-09-01")
        self.assertEqual([u.pk for u in task.assignees.all()], [self.member.pk])
        self.assertEqual(task.status.category, TaskStatus.Category.BACKLOG)
        self.assertTrue(task.events.filter(type=TaskEvent.Type.CREATED).exists())

    def test_create_task_defaults_to_personal_project(self):
        result = self._call(
            "create_task", CreateTaskParams(title="Buy milk"), self.member
        )
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertTrue(personal.tasks.filter(title="Buy milk").exists())
        self.assertIn("Personal", result)

    def test_create_task_matches_project_by_key(self):
        self._call(
            "create_task",
            CreateTaskParams(title="By key", project=self.project.key.lower()),
            self.member,
        )
        self.assertTrue(self.project.tasks.filter(title="By key").exists())

    def test_create_task_rejects_unknown_project_and_priority(self):
        result = self._call(
            "create_task",
            CreateTaskParams(title="X", project="Nope"),
            self.member,
        )
        self.assertIn('no project named "Nope"', result)
        self.assertIn("Website", result)

        result = self._call(
            "create_task",
            CreateTaskParams(title="X", project="Website", priority="asap"),
            self.member,
        )
        self.assertIn("invalid priority", result)
        self.assertFalse(self.project.tasks.filter(title="X").exists())

    def test_create_task_rejects_non_member_assignee(self):
        result = self._call(
            "create_task",
            CreateTaskParams(title="X", project="Website", assignee="outsider1"),
            self.member,
        )
        self.assertIn("not a member", result)
        self.assertFalse(self.project.tasks.filter(title="X").exists())

    def test_create_task_inaccessible_project_looks_unknown(self):
        result = self._call(
            "create_task",
            CreateTaskParams(title="X", project="Website"),
            self.outsider,
        )
        self.assertIn('no project named "Website"', result)

    # -- move_task -----------------------------------------------------------

    def test_move_task_by_status_name_marks_done(self):
        task = create_task(self.project, self.admin, title="Finish me")
        result = self._call(
            "move_task",
            MoveTaskParams(task_uuid=task.uuid, status="done"),
            self.member,
        )
        task.refresh_from_db()
        self.assertEqual(task.status.name, "Done")
        self.assertIsNotNone(task.completed_at)
        self.assertTrue(task.events.filter(type=TaskEvent.Type.COMPLETED).exists())
        self.assertIn('"Backlog" to "Done"', result)

    def test_move_task_unknown_status_lists_columns(self):
        task = create_task(self.project, self.admin, title="T")
        result = self._call(
            "move_task",
            MoveTaskParams(task_uuid=task.uuid, status="Doing"),
            self.member,
        )
        self.assertIn('no status "Doing"', result)
        self.assertIn("Backlog, To do, In progress, Done", result)
        task.refresh_from_db()
        self.assertEqual(task.status.name, "Backlog")

    def test_move_task_hidden_from_outsider(self):
        task = create_task(self.project, self.admin, title="T")
        result = self._call(
            "move_task",
            MoveTaskParams(task_uuid=task.uuid, status="Done"),
            self.outsider,
        )
        self.assertEqual(result, "Error: task not found.")

    def test_move_task_blocked_on_archived_project(self):
        task = create_task(self.project, self.admin, title="T")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        result = self._call(
            "move_task",
            MoveTaskParams(task_uuid=task.uuid, status="Done"),
            self.member,
        )
        self.assertIn("archived", result)
        task.refresh_from_db()
        self.assertEqual(task.status.name, "Backlog")

    def test_move_task_params_reject_malformed_uuid(self):
        with self.assertRaises(ValidationError):
            MoveTaskParams(task_uuid="not-a-uuid", status="Done")

    # -- update_task ---------------------------------------------------------

    def test_update_task_assigns_and_sets_due_date(self):
        task = create_task(self.project, self.admin, title="Triage me")
        result = self._call(
            "update_task",
            UpdateTaskParams(
                task_uuid=task.uuid, assignee="member1", due_date="2026-09-15"
            ),
            self.admin,
        )
        task.refresh_from_db()
        self.assertEqual(task.due_date.isoformat(), "2026-09-15")
        self.assertEqual([u.pk for u in task.assignees.all()], [self.member.pk])
        self.assertTrue(task.events.filter(type=TaskEvent.Type.ASSIGNED).exists())
        self.assertTrue(task.events.filter(type=TaskEvent.Type.UPDATED).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.member).exists())
        self.assertIn("assigned to member1", result)

    def test_update_task_clears_due_date(self):
        task = create_task(
            self.project,
            self.admin,
            title="T",
            due_date=timezone.localdate(),
        )
        result = self._call(
            "update_task",
            UpdateTaskParams(task_uuid=task.uuid, due_date="none"),
            self.member,
        )
        task.refresh_from_db()
        self.assertIsNone(task.due_date)
        self.assertIn("due date cleared", result)

    def test_update_task_requires_a_change(self):
        task = create_task(self.project, self.admin, title="T")
        result = self._call(
            "update_task", UpdateTaskParams(task_uuid=task.uuid), self.member
        )
        self.assertIn("nothing to update", result)

    def test_update_task_hidden_from_outsider(self):
        task = create_task(self.project, self.admin, title="T")
        result = self._call(
            "update_task",
            UpdateTaskParams(task_uuid=task.uuid, due_date="2026-09-15"),
            self.outsider,
        )
        self.assertEqual(result, "Error: task not found.")

    # -- comment_on_task -----------------------------------------------------

    def test_comment_on_task_creates_comment_event_and_notification(self):
        task = create_task(self.project, self.admin, title="Discuss me")
        result = self._call(
            "comment_on_task",
            CommentOnTaskParams(task_uuid=task.uuid, body="On it."),
            self.member,
        )
        comment = TaskComment.objects.get(task=task)
        self.assertEqual(comment.author, self.member)
        self.assertEqual(comment.body, "On it.")
        self.assertTrue(task.events.filter(type=TaskEvent.Type.COMMENTED).exists())
        # The task creator is in the implicit recipient set.
        self.assertTrue(Notification.objects.filter(recipient=self.admin).exists())
        self.assertIn(task.reference, result)

    def test_comment_on_task_hidden_from_outsider(self):
        task = create_task(self.project, self.admin, title="T")
        result = self._call(
            "comment_on_task",
            CommentOnTaskParams(task_uuid=task.uuid, body="Hi"),
            self.outsider,
        )
        self.assertEqual(result, "Error: task not found.")
        self.assertFalse(TaskComment.objects.exists())

    def test_unknown_task_uuid_reports_not_found(self):
        result = self._call(
            "comment_on_task",
            CommentOnTaskParams(task_uuid=uuid.uuid4(), body="Hi"),
            self.member,
        )
        self.assertEqual(result, "Error: task not found.")
