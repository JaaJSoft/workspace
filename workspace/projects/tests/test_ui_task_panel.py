import uuid as uuid_module

from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Label
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin


class TaskPanelViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        # One label so the panel's labels section (and its set_labels gating)
        # actually renders in the disabled-attribute counts below.
        self.label = Label.objects.create(project=self.project, name="bug")
        self.task = create_task(
            self.project,
            self.admin,
            title="Ship the panel",
            status=self.todo,
            description="**bold** move",
        )
        self.url = f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/panel"

    def test_member_gets_panel(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "projects/ui/partials/task_panel.html")
        self.assertContains(resp, 'id="task-panel"')
        self.assertContains(resp, "Ship the panel")
        self.assertContains(resp, "<strong>bold</strong>")

    def test_description_html_is_escaped(self):
        task = create_task(
            self.project,
            self.admin,
            title="XSS attempt",
            status=self.todo,
            description='<script>alert("x")</script>',
        )
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/tasks/{task.uuid}/panel")
        self.assertNotContains(resp, "<script>alert")
        self.assertContains(resp, "&lt;script&gt;")

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_task_from_another_project_is_404(self):
        other = create_project(self.admin, name="Other project")
        other_task = create_task(other, self.admin, title="Elsewhere")
        self.client.force_login(self.member)
        resp = self.client.get(
            f"/projects/{self.project.uuid}/tasks/{other_task.uuid}/panel"
        )
        self.assertEqual(resp.status_code, 404)

    def test_member_gets_task_actions(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertIn("edit", resp.context["panel_action_ids"])
        self.assertIn("move", resp.context["panel_action_ids"])
        self.assertContains(resp, "Delete task")
        # Inverse of the archived case: every control is writable, so the
        # template must not emit a single disabled attribute.
        self.assertNotContains(resp, "disabled")

    def test_archived_project_renders_read_only(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["panel_action_ids"], [])
        self.assertNotContains(resp, "Delete task")
        # No action is available on an archived project, so every gated
        # control must be disabled: the status, priority and due-date fields
        # and one checkbox per label. Assignees are chips + selector, both
        # hidden client-side via can('assign'), so they carry no disabled
        # attribute.
        expected_disabled = 3 + 1
        self.assertContains(resp, "disabled", count=expected_disabled)

    def test_activity_feed_uses_normalized_events(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Task created")
        events = resp.context["panel_events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_color"], "accent")
        self.assertIn("time_ago", events[0])
        # Panel activity rows hide the redundant per-row View link.
        self.assertNotContains(resp, ">View</a>")

    def test_panel_data_includes_assignee_users(self):
        self.task.assignees.add(self.member)
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(
            resp.context["panel_task_data"]["assignee_users"],
            [{"id": str(self.member.pk), "username": "member1"}],
        )

    def test_panel_task_data_embedded_as_json_script(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="task-panel-data"')
        self.assertContains(resp, 'id="task-panel-actions"')
        self.assertEqual(resp.context["panel_task_data"]["uuid"], str(self.task.uuid))

    def test_unknown_task_uuid_is_404(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            f"/projects/{self.project.uuid}/tasks/{uuid_module.uuid4()}/panel"
        )
        self.assertEqual(resp.status_code, 404)


class TaskDeepLinkTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Deep linked")
        self.board_url = f"/projects/{self.project.uuid}/board"

    def test_valid_task_param_renders_panel(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"{self.board_url}?task={self.task.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["panel_task"], self.task)
        self.assertContains(resp, 'id="task-panel"')
        self.assertContains(resp, "Deep linked")

    def test_malformed_task_param_is_ignored(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"{self.board_url}?task=not-a-uuid")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("panel_task", resp.context)

    def test_unknown_task_param_is_ignored(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"{self.board_url}?task={uuid_module.uuid4()}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("panel_task", resp.context)

    def test_other_projects_task_param_is_ignored(self):
        other = create_project(self.admin, name="Second project")
        foreign = create_task(other, self.admin, title="Foreign task")
        self.client.force_login(self.member)
        resp = self.client.get(f"{self.board_url}?task={foreign.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("panel_task", resp.context)

    def test_partial_swap_ignores_task_param(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            f"{self.board_url}?task={self.task.uuid}", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(resp, "projects/ui/partials/_content.html")
        self.assertNotIn("panel_task", resp.context)

    def test_overview_supports_deep_link(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}?task={self.task.uuid}")
        self.assertEqual(resp.context["panel_task"], self.task)
