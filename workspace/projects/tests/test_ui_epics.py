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

    def test_modal_epic_dropdown_renders_from_the_reactive_list(self):
        # The rows are Alpine-rendered from the shared epics list, so an
        # epic created in the settings (or inline) is pickable with no
        # reload; members hide the empty field, admins keep it to create
        # the first epic from the menu.
        self.client.force_login(self.member)
        resp = self.board()
        self.assertContains(resp, "form.epic === epic.uuid")
        self.assertContains(resp, 'x-show="openEpics().length"')
        self.assertNotContains(resp, "New epic...")
        self.client.force_login(self.admin)
        resp = self.board()
        self.assertNotContains(resp, 'x-show="openEpics().length"')
        self.assertContains(resp, "New epic...")
        self.assertContains(resp, "createEpic(newEpic)")


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

    def test_panel_offers_the_epic_dropdown(self):
        # Single-value field: the panel renders the same dropdown as status
        # and priority, committing through the set_epic-gated field patch.
        # Rows come from the reactive epics list (openEpics()), not the
        # server, so the menu tracks settings changes without a reload.
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "commitField('epic', data.epic || null)")
        self.assertContains(resp, "No epic")
        self.assertContains(resp, "data.epic === epic.uuid")

    def test_member_field_hides_with_nothing_to_offer_admin_keeps_it(self):
        # Members only pick, so their field carries the reactive empty gate;
        # admins always see it - the menu's inline create needs a host.
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'x-show="openEpics().length || data.epic"')
        self.assertNotContains(resp, "New epic...")
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'x-show="openEpics().length || data.epic"')
        self.assertContains(resp, "New epic...")
        self.assertContains(resp, "createEpic(newEpic)")


class SettingsEpicTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_settings_page_has_epics_section(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(resp, 'id="settings-epics"')
        self.assertContains(resp, "projectEpics(")

    def test_settings_epics_list_has_search_and_open_only_filter(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(resp, 'placeholder="Search epics"')
        self.assertContains(resp, 'x-model="openOnly"')
        self.assertContains(resp, 'x-for="epic in visibleEpics()"')
