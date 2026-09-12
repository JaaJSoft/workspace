from datetime import date

from django.test import TestCase

from workspace.projects.models import Milestone, Project, Sprint
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task
from workspace.projects.ui.views import TASK_RENDER_LIMIT

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin


class TimelineViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/projects/{self.project.uuid}/timeline"
        self.beta = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )

    def test_member_gets_the_page_with_the_chart(self):
        task = create_task(
            self.project, self.admin, title="Build", due_date=date(2026, 9, 20)
        )
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="task-collection"')
        self.assertContains(resp, f'data-row-id="{task.uuid}"')
        self.assertContains(resp, "<title>Beta</title>", html=False)

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_fragment_request_renders_the_content_partial_only(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url, headers={"X-Alpine-Request": "true"})
        self.assertContains(resp, 'id="project-content"')
        self.assertNotContains(resp, "<html")

    def test_filters_narrow_the_rows(self):
        kept = create_task(
            self.project,
            self.admin,
            title="Kept",
            due_date=date(2026, 9, 20),
            priority="high",
        )
        dropped = create_task(
            self.project, self.admin, title="Dropped", due_date=date(2026, 9, 21)
        )
        self.client.force_login(self.member)
        resp = self.client.get(self.url, {"priority": "high"})
        self.assertContains(resp, f'data-row-id="{kept.uuid}"')
        self.assertNotContains(resp, f'data-row-id="{dropped.uuid}"')

    def test_malformed_filter_is_400(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url, {"epic": "nope"}).status_code, 400)

    def test_unknown_scale_and_group_fall_back_to_defaults(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url, {"scale": "decade", "group": "colour"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["scale"], "week")
        self.assertEqual(resp.context["grouping"], "milestone")

    def test_scale_links_keep_the_filters(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url, {"priority": "high", "scale": "month"})
        self.assertContains(resp, "scale=quarter")
        html = resp.content.decode()
        quarter_link = [line for line in html.splitlines() if "scale=quarter" in line][
            0
        ]
        self.assertIn("priority=high", quarter_link)

    def test_undated_and_clipped_counts_are_reported(self):
        create_task(self.project, self.admin, title="No dates")
        create_task(self.project, self.admin, title="Far", due_date=date(2031, 1, 1))
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "1 task has no dates")
        self.assertContains(resp, "1 task falls outside")

    def test_render_cap_notice(self):
        for i in range(TASK_RENDER_LIMIT + 1):
            create_task(
                self.project, self.admin, title=f"t{i}", due_date=date(2026, 9, 20)
            )
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, f"Only the first {TASK_RENDER_LIMIT} tasks")

    def test_empty_state_without_dated_tasks_or_milestones(self):
        self.beta.delete()
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Nothing to place on the timeline yet")

    def test_sprint_bands_only_on_scrum(self):
        scrum = create_project(self.admin, name="S", project_type=Project.Type.SCRUM)
        Sprint.objects.create(
            project=scrum,
            name="Sprint 1",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 20),
        )
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{scrum.uuid}/timeline")
        self.assertContains(resp, "<title>Sprint 1</title>", html=False)
        kanban = self.client.get(self.url)
        self.assertNotContains(kanban, "Sprint band")

    def test_drawer_links_to_the_timeline(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'data-lucide="chart-gantt"')
        self.assertContains(resp, "currentView === 'timeline'")
