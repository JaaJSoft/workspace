import re
import uuid as uuid_module
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Label
from workspace.projects.services.links import create_link
from workspace.projects.services.projects import create_project
from workspace.projects.services.subtasks import create_subtask
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
        # template must not emit a single server-side disabled attribute
        # (a bare "disabled" closing the tag; the comment form's client-side
        # :disabled binding is always present and intentionally not matched).
        self.assertEqual(self._count_disabled(resp), 0)
        self.assertContains(resp, "Add a comment...")

    @staticmethod
    def _count_disabled(resp):
        # Server-side disabled attributes closing a tag ("disabled>" or
        # "disabled />" on self-closed void tags), format-agnostic.
        return len(re.findall(r"\sdisabled\s*/?>", resp.content.decode()))

    def test_archived_project_renders_read_only(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["panel_action_ids"], [])
        self.assertNotContains(resp, "Delete task")
        # No action is available on an archived project, so every gated
        # control must be disabled: the status, priority and due-date fields
        # and the checklist item checkbox. Assignees and labels render as
        # chips whose remove controls and selectors are omitted entirely, so
        # they carry no disabled attribute.
        self.assertEqual(self._count_disabled(resp), 4)
        # The comment form is gated on the "comment" action, absent when archived.
        self.assertNotContains(resp, "Add a comment...")

    def test_admin_gets_the_label_selector_with_create_endpoint(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="panel-labels-data"')
        self.assertContains(resp, "labelSelector(")
        self.assertContains(resp, f"/api/v1/projects/{self.project.uuid}/labels")

    def test_member_gets_the_selector_without_the_create_endpoint(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "labelSelector(")
        self.assertNotContains(resp, f"/api/v1/projects/{self.project.uuid}/labels")

    def test_labels_section_hidden_for_member_when_project_has_none(self):
        self.label.delete()
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "labelSelector(")

    def test_labels_section_rendered_for_admin_when_project_has_none(self):
        # Admins can create the first label straight from the task panel.
        self.label.delete()
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, "labelSelector(")

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

    def test_panel_data_includes_the_checklist(self):
        first = create_subtask(self.task, "Write the code")
        done = create_subtask(self.task, "Review it")
        done.done = True
        done.save(update_fields=["done"])
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        data = resp.context["panel_task_data"]
        self.assertEqual(
            data["subtasks"],
            [
                {"uuid": str(first.uuid), "title": "Write the code", "done": False},
                {"uuid": str(done.uuid), "title": "Review it", "done": True},
            ],
        )
        self.assertEqual(
            data["subtasks_url"],
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/subtasks",
        )

    def test_panel_shows_the_task_reference(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, f"{self.project.key}-{self.task.number}")

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

    def test_reference_deep_link_opens_the_panel(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            f"/projects/{self.project.uuid}?task={self.project.key}-{self.task.number}"
        )
        self.assertEqual(resp.context["panel_task"], self.task)
        self.assertContains(resp, self.task.title)

    def test_lowercase_reference_works(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            f"/projects/{self.project.uuid}"
            f"?task={self.project.key.lower()}-{self.task.number}"
        )
        self.assertEqual(resp.context["panel_task"], self.task)

    def test_reference_key_must_match_the_project(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            f"/projects/{self.project.uuid}?task=ZZZZ-{self.task.number}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("panel_task", resp.context)

    def test_garbage_task_param_is_ignored(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}?task=blah")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("panel_task", resp.context)


class TaskPanelEstimateTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_panel_payload_carries_the_estimate(self):
        self.project.estimate_unit = "points"
        self.project.save(update_fields=["estimate_unit"])
        task = create_task(
            self.project, self.admin, title="Sized", estimate=Decimal("3.5")
        )
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/tasks/{task.uuid}/panel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["panel_task_data"]["estimate"], "3.5")
        self.assertContains(resp, "Estimate (story points)")

    def test_estimate_field_absent_when_estimation_is_disabled(self):
        task = create_task(self.project, self.admin, title="Plain")
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/tasks/{task.uuid}/panel")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Estimate (")


class TaskPanelLinksTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Anchor")
        self.other = create_task(self.project, self.admin, title="Dependency")
        self.url = f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/panel"

    def test_panel_embeds_the_serialized_links(self):
        create_link(self.other, self.task, "blocks")
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="task-panel-links"')
        (item,) = resp.context["panel_links"]
        self.assertEqual(item["label"], "is blocked by")
        self.assertEqual(item["task"]["uuid"], str(self.other.uuid))

    def test_panel_data_carries_the_link_endpoints(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        data = resp.context["panel_task_data"]
        self.assertEqual(
            data["links_url"],
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/links",
        )
        self.assertEqual(data["link_search_url"], "/api/v1/projects/tasks/search")

    def test_member_gets_the_link_picker(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertIn("link", resp.context["panel_action_ids"])
        self.assertContains(resp, 'x-model="linkRel"')

    def test_archived_project_hides_the_link_picker(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertNotIn("link", resp.context["panel_action_ids"])
        self.assertNotContains(resp, 'x-model="linkRel"')
