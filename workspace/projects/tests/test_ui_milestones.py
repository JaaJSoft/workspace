from datetime import date

from django.test import TestCase

from workspace.projects.models import Milestone
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin


class MilestoneIslandTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.beta = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        self.todo = self.project.statuses.get(name="To do")

    def test_project_page_embeds_the_milestones_payload(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(resp, 'id="milestones-data"')
        self.assertContains(resp, str(self.beta.uuid))
        self.assertContains(resp, "2026-10-01")

    def test_panel_carries_start_date_and_milestone(self):
        task = create_task(
            self.project,
            self.admin,
            title="t",
            status=self.todo,
            start_date=date(2026, 9, 1),
            milestone=self.beta,
        )
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/tasks/{task.uuid}/panel")
        self.assertContains(resp, 'id="panel-milestones-data"')
        self.assertContains(resp, '"start_date": "2026-09-01"')
        self.assertContains(resp, f'"milestone": "{self.beta.uuid}"')
        self.assertContains(resp, 'x-model="data.start_date"')
        self.assertContains(resp, "No milestone")

    def test_panel_picker_is_disabled_on_an_archived_project(self):
        from django.utils import timezone

        task = create_task(self.project, self.admin, title="t", status=self.todo)
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/tasks/{task.uuid}/panel")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("set_milestone", resp.context["panel_action_ids"])

    def test_create_modal_offers_start_date_and_milestone(self):
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(resp, 'x-model="form.start_date"')
        self.assertContains(resp, "form.milestone")


class SettingsMilestonesCardTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_admin_sees_the_milestones_card(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(resp, 'id="settings-milestones"')
        self.assertContains(resp, "projectMilestones(")
        self.assertContains(resp, "Add milestone")
