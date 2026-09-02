from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, Task, TaskEvent
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.sprints import complete_sprint, start_sprint
from workspace.projects.services.tasks import create_task, move_tasks
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


class FlowReportsContentTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    @property
    def url(self):
        return f"/projects/{self.project.uuid}/analytics"

    def test_finished_tasks_feed_the_cycle_time_section(self):
        done = self.project.statuses.get(name="Done")
        task = create_task(self.project, self.admin, title="Work")
        move_tasks(self.project, done, [task.uuid])
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.context["duration_stats"]["count"], 1)
        chart = response.context["duration_chart"]
        self.assertEqual(
            [s["name"] for s in chart["series"]], ["Lead time", "Cycle time"]
        )
        self.assertIn("task measured", response.content.decode())

    def test_nothing_finished_shows_the_section_placeholder(self):
        create_task(self.project, self.admin, title="Open")
        self.client.force_login(self.member)
        html = self.client.get(self.url).content.decode()
        self.assertIn("No task finished in the last 12 weeks yet.", html)

    def test_cumulative_flow_covers_twelve_weeks_of_days(self):
        create_task(self.project, self.admin, title="Work")
        self.client.force_login(self.member)
        chart = self.client.get(self.url).context["cumulative_flow_chart"]
        self.assertEqual(
            [s["name"] for s in chart["series"]], ["Done", "Active", "Backlog"]
        )
        self.assertEqual(chart["series"][0]["segments"][0]["line"].count(","), 84)

    def test_kanban_projects_have_no_sprint_sections(self):
        create_task(self.project, self.admin, title="Work")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertNotIn("Sprint burndown", html)
        self.assertNotIn("Velocity", html)
        self.assertNotIn("burndown", response.context)

    def test_empty_project_skips_the_replays_entirely(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertNotIn("cumulative_flow_chart", response.context)


class SprintReportsContentTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        add_member(self.scrum, self.member)
        self.todo = self.scrum.statuses.get(name="To do")
        self.done = self.scrum.statuses.get(name="Done")
        self.client.force_login(self.member)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @property
    def url(self):
        return f"/projects/{self.scrum.uuid}/analytics"

    def test_sprint_history_older_than_the_window_still_renders_velocity(self):
        # No open task and no movement in twelve weeks: the flow charts have
        # nothing to say, yet the sprint history is worth a page.
        sprint = self._closed_sprint("Long ago")
        TaskEvent.objects.update(created_at=timezone.now() - timedelta(weeks=20))
        response = self.client.get(self.url)
        self.assertFalse(response.context["is_empty"])
        self.assertEqual(response.context["velocity"][0]["sprint"], sprint)
        self.assertEqual(response.context["flow_summary"]["completed"], 0)
        self.assertNotIn("Nothing to chart yet", response.content.decode())

    def test_a_scrum_project_without_sprints_or_tasks_is_still_empty(self):
        response = self.client.get(self.url)
        self.assertTrue(response.context["is_empty"])
        self.assertEqual(response.context["velocity"], [])

    def _closed_sprint(self, name, *, done_tasks=1):
        sprint = self.scrum.sprints.create(name=name)
        for _ in range(done_tasks):
            create_task(
                self.scrum, self.admin, title="Done", sprint=sprint, status=self.todo
            )
        start_sprint(sprint, actor=self.admin)
        move_tasks(
            self.scrum, self.done, list(sprint.tasks.values_list("uuid", flat=True))
        )
        complete_sprint(sprint, actor=self.admin)
        return sprint

    def test_without_sprints_both_sections_show_their_placeholders(self):
        create_task(self.scrum, self.admin, title="Work")
        html = self.client.get(self.url).content.decode()
        self.assertIn("Start a sprint to see its burndown here.", html)
        self.assertIn("Velocity appears once a sprint has been completed.", html)

    def test_running_sprint_is_the_default_burndown(self):
        self._closed_sprint("Sprint 1")
        active = self.scrum.sprints.create(name="Sprint 2")
        create_task(
            self.scrum, self.admin, title="Now", sprint=active, status=self.todo
        )
        start_sprint(active, actor=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.context["report_sprint"], active)
        self.assertEqual(response.context["burndown"]["remaining"], 1)
        self.assertEqual(
            [s["name"] for s in response.context["burndown_chart"]["series"]],
            ["Ideal", "Remaining"],
        )

    def test_sprint_param_picks_a_closed_sprint(self):
        first = self._closed_sprint("Sprint 1", done_tasks=2)
        self._closed_sprint("Sprint 2")
        response = self.client.get(f"{self.url}?sprint={first.uuid}")
        self.assertEqual(response.context["report_sprint"], first)
        self.assertEqual(response.context["burndown"]["scope"], 2)

    def test_unknown_or_malformed_sprint_param_falls_back_to_the_latest(self):
        self._closed_sprint("Sprint 1")
        latest = self._closed_sprint("Sprint 2")
        for param in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
            response = self.client.get(f"{self.url}?sprint={param}")
            self.assertEqual(response.context["report_sprint"], latest)

    def test_planned_sprints_are_not_offered(self):
        self._closed_sprint("Sprint 1")
        planned = self.scrum.sprints.create(name="Someday")
        response = self.client.get(self.url)
        self.assertNotIn(planned, response.context["report_sprints"])
        self.assertNotIn(f"?sprint={planned.uuid}", response.content.decode())

    def test_velocity_lists_closed_sprints_oldest_first(self):
        self._closed_sprint("Sprint 1", done_tasks=2)
        self._closed_sprint("Sprint 2", done_tasks=3)
        response = self.client.get(self.url)
        chart = response.context["velocity_chart"]
        self.assertEqual(
            [c["label"] for c in chart["categories"]], ["Sprint 1", "Sprint 2"]
        )
        self.assertEqual(response.context["velocity_summary"]["last"], 3)
        self.assertIn("tasks completed", response.content.decode())

    def test_velocity_reads_points_when_the_project_estimates(self):
        self.scrum.estimate_unit = Project.EstimateUnit.POINTS
        self.scrum.save(update_fields=["estimate_unit"])
        sprint = self.scrum.sprints.create(name="Sprint 1")
        create_task(
            self.scrum,
            self.admin,
            title="Big",
            sprint=sprint,
            status=self.todo,
            estimate=Decimal("5.5"),
        )
        start_sprint(sprint, actor=self.admin)
        move_tasks(
            self.scrum, self.done, list(sprint.tasks.values_list("uuid", flat=True))
        )
        complete_sprint(sprint, actor=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.context["velocity_unit"], "points")
        self.assertEqual(
            response.context["velocity_chart"]["bars"][0]["tooltip"],
            "Sprint 1: 5.5 completed",
        )
        self.assertIn("5.5", response.content.decode())

    def test_burndown_switcher_links_back_to_the_analytics_view(self):
        sprint = self._closed_sprint("Sprint 1")
        html = self.client.get(self.url).content.decode()
        self.assertIn(f"{self.url}?sprint={sprint.uuid}", html)
