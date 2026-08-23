from django.test import TestCase

from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin


class BoardEpicTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.epic = self.project.epics.create(name="Launch", color="#3b82f6")
        self.todo = self.project.statuses.get(name="To do")

    def board(self):
        return self.client.get(f"/projects/{self.project.uuid}/board")

    def test_page_embeds_the_epics_payload(self):
        self.client.force_login(self.member)
        resp = self.board()
        self.assertContains(resp, 'id="epics-data"')
        self.assertContains(resp, str(self.epic.uuid))

    def test_card_shows_the_epic_badge(self):
        create_task(
            self.project, self.admin, title="Grouped", status=self.todo, epic=self.epic
        )
        self.client.force_login(self.member)
        resp = self.board()
        self.assertContains(resp, 'name="Launch"')
        self.assertContains(resp, 'icon="layers"')

    def test_board_filters_by_epic_server_side(self):
        kept = create_task(
            self.project, self.admin, title="Inside", status=self.todo, epic=self.epic
        )
        dropped = create_task(
            self.project, self.admin, title="Outside", status=self.todo
        )
        self.client.force_login(self.member)
        resp = self.client.get(
            f"/projects/{self.project.uuid}/board", {"epic": str(self.epic.uuid)}
        )
        self.assertContains(resp, f'data-task-uuid="{kept.uuid}"')
        self.assertNotContains(resp, f'data-task-uuid="{dropped.uuid}"')

    def test_malformed_epic_filter_is_400(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            f"/projects/{self.project.uuid}/board", {"epic": "not-a-uuid"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_filter_bar_offers_the_epic_picker_without_create(self):
        self.client.force_login(self.admin)
        resp = self.board()
        self.assertContains(resp, 'placeholder="Filter by epic"')
        html = resp.content.decode()
        filter_bar = html.split('id="task-filters"')[1].split('id="task-collection"')[0]
        self.assertIn("labelSelector('filter-epic-selected'", filter_bar)
        # The filter picker must not offer epic creation, even to admins:
        # the x-data call ends with an empty create URL.
        epic_picker = filter_bar.split("labelSelector('filter-epic-selected'")[1]
        call_args = epic_picker.split('"\n')[0]
        self.assertIn(", ''", call_args)
        self.assertNotIn(f"/api/v1/projects/{self.project.uuid}/epics", call_args)

    def test_filter_bar_hides_the_epic_picker_without_epics(self):
        self.epic.delete()
        self.client.force_login(self.member)
        self.assertNotContains(self.board(), 'placeholder="Filter by epic"')

    def test_modal_epic_picker_gating_mirrors_labels(self):
        # Members cannot create epics, so an empty project hides the field
        # client-side; admins always see it (they can create the first epic).
        self.client.force_login(self.member)
        self.assertContains(self.board(), 'x-show="epics.length"')
        self.client.force_login(self.admin)
        self.assertNotContains(self.board(), 'x-show="epics.length"')


class BacklogEpicTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_backlog_row_shows_the_epic_badge(self):
        epic = self.project.epics.create(name="Launch", color="#3b82f6")
        create_task(self.project, self.admin, title="Queued", epic=epic)
        self.client.force_login(self.member)
        resp = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertContains(resp, 'name="Launch"')
        self.assertContains(resp, 'icon="layers"')


class TaskPanelEpicTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.epic = self.project.epics.create(name="Launch", color="#3b82f6")
        self.task = create_task(
            self.project, self.admin, title="Grouped", epic=self.epic
        )
        self.url = f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/panel"

    def test_panel_embeds_the_epics_payload(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="panel-epics-data"')
        self.assertContains(resp, "setEpic($event.detail.label)")

    def test_admin_gets_the_epic_selector_with_create_endpoint(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, "labelSelector('panel-epic-selected'")
        self.assertContains(resp, f"/api/v1/projects/{self.project.uuid}/epics")

    def test_member_gets_the_selector_without_the_create_endpoint(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "labelSelector('panel-epic-selected'")
        self.assertNotContains(resp, f"/api/v1/projects/{self.project.uuid}/epics")

    def test_epic_section_hidden_for_member_when_project_has_none(self):
        self.task.epic = None
        self.task.save(update_fields=["epic"])
        self.epic.delete()
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "labelSelector('panel-epic-selected'")

    def test_epic_section_rendered_for_admin_when_project_has_none(self):
        # Admins can create the first epic straight from the task panel.
        self.task.epic = None
        self.task.save(update_fields=["epic"])
        self.epic.delete()
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, "labelSelector('panel-epic-selected'")


class SettingsEpicTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_settings_page_has_epics_section(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(resp, 'id="settings-epics"')
        self.assertContains(resp, "projectEpics(")
