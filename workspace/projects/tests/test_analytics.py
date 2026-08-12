from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Task, TaskEvent, TaskStatus
from workspace.projects.services.analytics import (
    flow_summary,
    open_task_distribution,
    weekly_flow,
)
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task, delete_task

from .base import ProjectTestMixin


class WeeklyFlowTests(ProjectTestMixin, TestCase):
    def _event(self, type, *, project=None, weeks_ago=0, title="T"):
        """TaskEvent.created_at is auto_now_add, so backdating an event
        needs an UPDATE after the insert."""
        event = TaskEvent.objects.create(
            project=project or self.project, task_title=title, type=type
        )
        if weeks_ago:
            TaskEvent.objects.filter(pk=event.pk).update(
                created_at=timezone.now() - timedelta(weeks=weeks_ago)
            )
        return event

    def test_returns_exactly_the_requested_number_of_weeks(self):
        self.assertEqual(len(weekly_flow(self.project, weeks=12)), 12)

    def test_buckets_are_oldest_first_and_one_week_apart(self):
        starts = [r["week_start"] for r in weekly_flow(self.project, weeks=4)]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual((starts[1] - starts[0]).days, 7)

    def test_every_bucket_starts_on_a_monday(self):
        for row in weekly_flow(self.project, weeks=4):
            self.assertEqual(row["week_start"].weekday(), 0)

    def test_counts_created_and_completed_in_the_current_week(self):
        self._event(TaskEvent.Type.CREATED)
        self._event(TaskEvent.Type.CREATED)
        self._event(TaskEvent.Type.COMPLETED)
        current = weekly_flow(self.project, weeks=4)[-1]
        self.assertEqual(current["created"], 2)
        self.assertEqual(current["completed"], 1)

    def test_quiet_weeks_are_filled_with_zeros_not_dropped(self):
        self._event(TaskEvent.Type.CREATED, weeks_ago=3)
        rows = weekly_flow(self.project, weeks=4)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["created"], 1)
        self.assertEqual([r["created"] for r in rows[1:]], [0, 0, 0])

    def test_events_older_than_the_window_are_excluded(self):
        self._event(TaskEvent.Type.CREATED, weeks_ago=10)
        rows = weekly_flow(self.project, weeks=4)
        self.assertEqual(sum(r["created"] for r in rows), 0)

    def test_ignores_event_types_that_are_not_created_or_completed(self):
        self._event(TaskEvent.Type.MOVED)
        self._event(TaskEvent.Type.COMMENTED)
        self._event(TaskEvent.Type.UPDATED)
        rows = weekly_flow(self.project, weeks=4)
        self.assertEqual(sum(r["created"] + r["completed"] for r in rows), 0)

    def test_ignores_other_projects(self):
        other = create_project(self.admin, name="Other")
        self._event(TaskEvent.Type.CREATED, project=other)
        rows = weekly_flow(self.project, weeks=4)
        self.assertEqual(sum(r["created"] for r in rows), 0)

    def test_events_survive_their_task_being_deleted(self):
        task = create_task(self.project, self.admin, title="Doomed")
        delete_task(task, actor=self.admin)
        rows = weekly_flow(self.project, weeks=4)
        self.assertEqual(rows[-1]["created"], 1)

    def test_a_recompleted_task_counts_once_per_completion(self):
        self._event(TaskEvent.Type.COMPLETED)
        self._event(TaskEvent.Type.COMPLETED)
        self.assertEqual(weekly_flow(self.project, weeks=4)[-1]["completed"], 2)


class FlowSummaryTests(TestCase):
    def test_totals_net_and_average(self):
        rows = [
            {"week_start": None, "created": 4, "completed": 1},
            {"week_start": None, "created": 2, "completed": 3},
        ]
        self.assertEqual(
            flow_summary(rows),
            {"created": 6, "completed": 4, "net": 2, "avg_weekly": 2.0},
        )

    def test_a_shrinking_backlog_reports_a_negative_net(self):
        rows = [{"week_start": None, "created": 1, "completed": 5}]
        self.assertEqual(flow_summary(rows)["net"], -4)

    def test_empty_input_does_not_divide_by_zero(self):
        self.assertEqual(
            flow_summary([]),
            {"created": 0, "completed": 0, "net": 0, "avg_weekly": 0},
        )


class OpenTaskDistributionTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")

    def test_by_status_counts_only_open_tasks_and_keeps_column_order(self):
        create_task(self.project, self.admin, title="A", status=self.todo)
        create_task(self.project, self.admin, title="B", status=self.done)
        by_status = open_task_distribution(self.project)["by_status"]
        self.assertEqual(
            [(e["label"], e["count"]) for e in by_status],
            [("Backlog", 0), ("To do", 1), ("In progress", 0)],
        )

    def test_by_status_excludes_done_columns_entirely(self):
        labels = [e["label"] for e in open_task_distribution(self.project)["by_status"]]
        self.assertNotIn("Done", labels)

    def test_unassigned_tasks_get_their_own_bucket(self):
        create_task(self.project, self.admin, title="Nobody's")
        by_assignee = open_task_distribution(self.project)["by_assignee"]
        self.assertEqual(
            [(e["label"], e["count"]) for e in by_assignee], [("Unassigned", 1)]
        )

    def test_a_task_with_two_assignees_counts_on_both_plates(self):
        create_task(
            self.project,
            self.admin,
            title="Shared",
            assignees=[self.admin, self.member],
        )
        by_assignee = open_task_distribution(self.project)["by_assignee"]
        self.assertEqual(
            sorted((e["label"], e["count"]) for e in by_assignee),
            [("admin1", 1), ("member1", 1)],
        )

    def test_unassigned_bucket_is_omitted_when_everything_is_assigned(self):
        create_task(self.project, self.admin, title="Mine", assignees=[self.admin])
        by_assignee = open_task_distribution(self.project)["by_assignee"]
        self.assertEqual([e["label"] for e in by_assignee], ["admin1"])

    def test_assignees_are_listed_busiest_first(self):
        create_task(self.project, self.admin, title="A", assignees=[self.member])
        create_task(self.project, self.admin, title="B", assignees=[self.member])
        create_task(self.project, self.admin, title="C", assignees=[self.admin])
        by_assignee = open_task_distribution(self.project)["by_assignee"]
        self.assertEqual([e["label"] for e in by_assignee], ["member1", "admin1"])

    def test_by_priority_lists_every_level_most_urgent_first(self):
        create_task(
            self.project, self.admin, title="Fire", priority=Task.Priority.URGENT
        )
        by_priority = open_task_distribution(self.project)["by_priority"]
        self.assertEqual(
            [(e["label"], e["count"]) for e in by_priority],
            [("Urgent", 1), ("High", 0), ("Medium", 0), ("Low", 0)],
        )

    def test_done_tasks_are_excluded_from_every_breakdown(self):
        create_task(self.project, self.admin, title="Shipped", status=self.done)
        distribution = open_task_distribution(self.project)
        self.assertEqual(sum(e["count"] for e in distribution["by_priority"]), 0)
        self.assertEqual(distribution["by_assignee"], [])

    def test_status_bars_carry_the_column_colour(self):
        TaskStatus.objects.filter(pk=self.todo.pk).update(color="#ff0000")
        by_status = open_task_distribution(self.project)["by_status"]
        todo = next(e for e in by_status if e["label"] == "To do")
        self.assertEqual(todo["color"], "#ff0000")

    def test_ignores_tasks_from_other_projects(self):
        other = create_project(self.admin, name="Other")
        create_task(other, self.admin, title="Elsewhere")
        distribution = open_task_distribution(self.project)
        self.assertEqual(sum(e["count"] for e in distribution["by_status"]), 0)
