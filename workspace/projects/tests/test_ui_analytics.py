from django.core.cache import cache
from django.test import TestCase

from workspace.projects.models import Task, TaskEvent
from workspace.projects.services.tasks import create_task
from workspace.users.services.settings import get_setting

from .base import ProjectTestMixin


class AnalyticsViewTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    @property
    def url(self):
        return f"/projects/{self.project.uuid}/analytics"

    def test_member_gets_the_page(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_full_page_render_uses_the_project_shell(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertTemplateUsed(response, "projects/ui/partials/analytics.html")

    def test_alpine_request_returns_only_the_content_partial(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url, headers={"X-Alpine-Request": "true"})
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")

    def test_sidebar_links_analytics_instead_of_advertising_it_as_soon(self):
        self.client.force_login(self.member)
        html = self.client.get(self.url).content.decode()
        self.assertIn(self.url, html)
        self.assertNotIn(">Soon<", html)

    def test_records_the_visit_so_the_index_redirect_comes_back_here(self):
        self.client.force_login(self.member)
        self.client.get(self.url)
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )

    def test_backlog_badge_still_renders_on_this_view(self):
        create_task(self.project, self.admin, title="Queued")
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).context["backlog_count"], 1)


class AnalyticsContentTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    @property
    def url(self):
        return f"/projects/{self.project.uuid}/analytics"

    def test_empty_project_shows_the_placeholder_not_charts(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertTrue(response.context["is_empty"])
        self.assertNotIn('viewBox="0 0 720', response.content.decode())
        self.assertIn("Nothing to chart yet", response.content.decode())

    def test_project_with_open_tasks_renders_the_charts(self):
        create_task(
            self.project, self.admin, title="Work", priority=Task.Priority.URGENT
        )
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertFalse(response.context["is_empty"])
        self.assertEqual(response.context["open_count"], 1)
        self.assertIn('viewBox="0 0 720', response.content.decode())

    def test_history_alone_is_enough_to_leave_the_empty_state(self):
        # Every task deleted, but the log still has something to say.
        TaskEvent.objects.create(
            project=self.project, task_title="Gone", type=TaskEvent.Type.COMPLETED
        )
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertFalse(response.context["is_empty"])
        self.assertEqual(response.context["flow_summary"]["completed"], 1)

    def test_flow_chart_covers_twelve_weeks_with_both_series(self):
        create_task(self.project, self.admin, title="Work")
        self.client.force_login(self.member)
        chart = self.client.get(self.url).context["flow_chart"]
        self.assertEqual(len(chart["categories"]), 12)
        self.assertEqual([s["name"] for s in chart["series"]], ["Created", "Completed"])

    def test_distribution_entries_carry_the_bar_maximum(self):
        create_task(self.project, self.admin, title="A", assignees=[self.admin])
        create_task(self.project, self.admin, title="B", assignees=[self.admin])
        create_task(self.project, self.admin, title="C", assignees=[self.member])
        self.client.force_login(self.member)
        by_assignee = self.client.get(self.url).context["distribution"]["by_assignee"]
        self.assertEqual([e["count"] for e in by_assignee], [2, 1])
        self.assertEqual({e["max_count"] for e in by_assignee}, {2})

    def test_an_all_zero_breakdown_never_divides_by_zero(self):
        TaskEvent.objects.create(
            project=self.project, task_title="Gone", type=TaskEvent.Type.COMPLETED
        )
        self.client.force_login(self.member)
        by_priority = self.client.get(self.url).context["distribution"]["by_priority"]
        self.assertEqual([e["count"] for e in by_priority], [0, 0, 0, 0])
        self.assertEqual({e["max_count"] for e in by_priority}, {1})

    def test_net_change_reports_a_growing_backlog(self):
        create_task(self.project, self.admin, title="A")
        create_task(self.project, self.admin, title="B")
        self.client.force_login(self.member)
        summary = self.client.get(self.url).context["flow_summary"]
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["net"], 2)

    def test_a_flat_net_reads_as_unchanged_not_shrinking(self):
        for event_type in (TaskEvent.Type.CREATED, TaskEvent.Type.COMPLETED):
            TaskEvent.objects.create(
                project=self.project, task_title="Churned", type=event_type
            )
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.context["flow_summary"]["net"], 0)
        html = response.content.decode()
        self.assertIn("backlog unchanged", html)
        self.assertNotIn("backlog shrinking", html)
        self.assertNotIn('data-lucide="trending-down"', html)

    def test_multiline_template_comments_do_not_leak_into_the_page(self):
        create_task(self.project, self.admin, title="Work")
        self.client.force_login(self.member)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("one-dimensional case", html)
        self.assertNotIn("Parameters:", html)
