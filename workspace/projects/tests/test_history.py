from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, Sprint, TaskEvent
from workspace.projects.services.history import (
    DURATION_BUCKETS,
    DurationSample,
    EventLog,
    cumulative_flow,
    duration_buckets,
    duration_summary,
    sprint_burndown,
    sprint_velocity,
    task_durations,
    velocity_summary,
)
from workspace.projects.services.projects import create_project
from workspace.projects.services.sprints import (
    assign_tasks_to_sprint,
    complete_sprint,
    start_sprint,
)
from workspace.projects.services.tasks import create_task, delete_task, move_tasks

from .base import ProjectTestMixin

DAY = timedelta(days=1)


def _shift(task, *, days, types=None):
    """Backdate a task's events: TaskEvent.created_at is auto_now_add, so
    the timeline has to be rewritten after the fact."""
    qs = TaskEvent.objects.filter(task=task)
    if types is not None:
        qs = qs.filter(type__in=types)
    for event in qs:
        TaskEvent.objects.filter(pk=event.pk).update(
            created_at=event.created_at - timedelta(days=days)
        )


class HistoryTestCase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.doing = self.project.statuses.get(name="In progress")
        self.done = self.project.statuses.get(name="Done")

    def _finished_task(self, *, created_days_ago, started_days_ago, done_days_ago):
        """A task created in the backlog, moved to the board and completed,
        each step backdated to the given number of days ago."""
        task = create_task(self.project, self.admin, title="T")
        _shift(task, days=created_days_ago, types=[TaskEvent.Type.CREATED])
        move_tasks(self.project, self.todo, [task.uuid])
        _shift(task, days=started_days_ago, types=[TaskEvent.Type.MOVED])
        move_tasks(self.project, self.done, [task.uuid])
        _shift(task, days=done_days_ago, types=[TaskEvent.Type.COMPLETED])
        return task


class TaskDurationsTests(HistoryTestCase):
    def test_lead_runs_from_creation_and_cycle_from_the_first_active_column(self):
        self._finished_task(created_days_ago=10, started_days_ago=4, done_days_ago=1)
        [sample] = task_durations(self.project)
        self.assertAlmostEqual(sample.lead / DAY, 9, places=3)
        self.assertAlmostEqual(sample.cycle / DAY, 3, places=3)

    def test_open_tasks_are_not_samples(self):
        create_task(self.project, self.admin, title="Open", status=self.doing)
        self.assertEqual(task_durations(self.project), [])

    def test_a_reopened_task_is_not_finished(self):
        task = self._finished_task(
            created_days_ago=5, started_days_ago=3, done_days_ago=1
        )
        move_tasks(self.project, self.doing, [task.uuid])
        self.assertEqual(task_durations(self.project), [])

    def test_a_task_finished_then_deleted_keeps_its_measurement(self):
        task = self._finished_task(
            created_days_ago=5, started_days_ago=3, done_days_ago=1
        )
        delete_task(task, actor=self.admin)
        [sample] = task_durations(self.project)
        self.assertAlmostEqual(sample.lead / DAY, 4, places=3)

    def test_straight_from_backlog_to_done_has_a_lead_but_no_cycle(self):
        task = create_task(self.project, self.admin, title="Skip")
        _shift(task, days=2)
        move_tasks(self.project, self.done, [task.uuid])
        [sample] = task_durations(self.project)
        self.assertIsNone(sample.cycle)
        self.assertGreater(sample.lead, DAY)

    def test_born_on_the_board_starts_its_cycle_at_creation(self):
        task = create_task(self.project, self.admin, title="Quick", status=self.todo)
        _shift(task, days=2)
        move_tasks(self.project, self.done, [task.uuid])
        [sample] = task_durations(self.project)
        self.assertEqual(sample.cycle, sample.lead)

    def test_a_reopen_and_recompletion_measures_to_the_last_completion(self):
        task = self._finished_task(
            created_days_ago=10, started_days_ago=8, done_days_ago=6
        )
        move_tasks(self.project, self.doing, [task.uuid])
        reopen = TaskEvent.objects.filter(task=task, type=TaskEvent.Type.MOVED).first()
        TaskEvent.objects.filter(pk=reopen.pk).update(
            created_at=reopen.created_at - timedelta(days=5)
        )
        move_tasks(self.project, self.done, [task.uuid])
        [sample] = task_durations(self.project)
        self.assertAlmostEqual(sample.lead / DAY, 10, places=3)
        # First entry on the board, eight days ago, not the reopen.
        self.assertAlmostEqual(sample.cycle / DAY, 8, places=3)

    def test_completions_older_than_the_window_are_left_out(self):
        self._finished_task(
            created_days_ago=120, started_days_ago=110, done_days_ago=100
        )
        self.assertEqual(task_durations(self.project, weeks=12), [])
        self.assertEqual(len(task_durations(self.project, weeks=20)), 1)

    def test_other_projects_do_not_leak_in(self):
        other = create_project(self.admin, name="Other")
        done = other.statuses.get(name="Done")
        create_task(other, self.admin, title="Theirs", status=done)
        self.assertEqual(task_durations(self.project), [])

    def test_legacy_events_without_category_snapshots_still_replay(self):
        task = self._finished_task(
            created_days_ago=6, started_days_ago=3, done_days_ago=1
        )
        TaskEvent.objects.filter(task=task).update(from_category="", to_category="")
        [sample] = task_durations(self.project)
        self.assertAlmostEqual(sample.cycle / DAY, 2, places=3)


class DurationSummaryTests(TestCase):
    def _sample(self, lead_days, cycle_days=None):
        return DurationSample(
            lead=timedelta(days=lead_days),
            cycle=timedelta(days=cycle_days) if cycle_days is not None else None,
        )

    def test_median_and_nearest_rank_p85_in_days(self):
        samples = [self._sample(d, d) for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
        summary = duration_summary(samples)
        self.assertEqual(summary["count"], 10)
        self.assertEqual(summary["lead"], {"median": 5.5, "p85": 9})
        self.assertEqual(summary["cycle"], {"median": 5.5, "p85": 9})

    def test_samples_without_a_cycle_only_count_toward_lead(self):
        summary = duration_summary([self._sample(4), self._sample(2, 1)])
        self.assertEqual(summary["lead"]["median"], 3)
        self.assertEqual(summary["cycle"]["median"], 1)

    def test_empty_input_yields_none_not_an_error(self):
        self.assertEqual(
            duration_summary([]),
            {
                "count": 0,
                "lead": {"median": None, "p85": None},
                "cycle": {"median": None, "p85": None},
            },
        )

    def test_a_single_sample_is_its_own_median_and_p85(self):
        self.assertEqual(
            duration_summary([self._sample(2.5, 1.25)])["cycle"],
            {"median": 1.2, "p85": 1.2},
        )

    def test_buckets_cover_every_duration_once(self):
        samples = [self._sample(d, d) for d in (0.5, 1, 3, 6, 10, 20, 90)]
        buckets = duration_buckets(samples)
        self.assertEqual(buckets["labels"], [label for label, _ in DURATION_BUCKETS])
        self.assertEqual(buckets["lead"], [1, 1, 1, 1, 1, 1, 1])
        self.assertEqual(sum(buckets["cycle"]), 7)

    def test_bucket_bounds_are_half_open(self):
        buckets = duration_buckets([self._sample(1), self._sample(0.999)])
        self.assertEqual(buckets["lead"][:2], [1, 1])


class CumulativeFlowTests(HistoryTestCase):
    def test_returns_one_row_per_day_oldest_first_ending_today(self):
        rows = cumulative_flow(self.project, days=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["date"], timezone.localdate())
        self.assertEqual((rows[1]["date"] - rows[0]["date"]).days, 1)

    def test_last_point_matches_the_current_board(self):
        create_task(self.project, self.admin, title="A")
        create_task(self.project, self.admin, title="B", status=self.doing)
        create_task(self.project, self.admin, title="C", status=self.done)
        today = cumulative_flow(self.project, days=3)[-1]
        self.assertEqual((today["backlog"], today["active"], today["done"]), (1, 1, 1))

    def test_a_task_moves_between_bands_on_the_day_it_moved(self):
        task = create_task(self.project, self.admin, title="A")
        _shift(task, days=3)
        move_tasks(self.project, self.todo, [task.uuid])
        _shift(task, days=1, types=[TaskEvent.Type.MOVED])
        rows = cumulative_flow(self.project, days=5)
        self.assertEqual([r["backlog"] for r in rows], [0, 1, 1, 0, 0])
        self.assertEqual([r["active"] for r in rows], [0, 0, 0, 1, 1])

    def test_a_deleted_task_leaves_its_band(self):
        task = create_task(self.project, self.admin, title="A", status=self.done)
        _shift(task, days=2)
        delete_task(task, actor=self.admin)
        rows = cumulative_flow(self.project, days=4)
        self.assertEqual([r["done"] for r in rows], [0, 1, 1, 0])

    def test_history_older_than_the_window_is_carried_into_the_first_day(self):
        task = create_task(self.project, self.admin, title="Old")
        _shift(task, days=400)
        rows = cumulative_flow(self.project, days=3)
        self.assertEqual([r["backlog"] for r in rows], [1, 1, 1])

    def test_events_of_other_projects_are_ignored(self):
        other = create_project(self.admin, name="Other")
        create_task(other, self.admin, title="Theirs")
        self.assertEqual(cumulative_flow(self.project, days=2)[-1]["backlog"], 0)


class ScrumHistoryTestCase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        self.backlog = self.scrum.statuses.get(name="Backlog")
        self.todo = self.scrum.statuses.get(name="To do")
        self.done = self.scrum.statuses.get(name="Done")
        self.today = timezone.localdate()

    def _sprint(self, name="Sprint 1", *, days=5, started_days_ago=None, **fields):
        sprint = self.scrum.sprints.create(name=name, **fields)
        if started_days_ago is not None:
            sprint.start_date = self.today - timedelta(days=started_days_ago)
            sprint.end_date = sprint.start_date + timedelta(days=days - 1)
            sprint.state = Sprint.State.ACTIVE
            sprint.save()
        return sprint

    def _estimating(self):
        self.scrum.estimate_unit = Project.EstimateUnit.POINTS
        self.scrum.save(update_fields=["estimate_unit"])


class SprintBurndownTests(ScrumHistoryTestCase):
    def test_one_row_per_sprint_day_with_future_days_blank(self):
        sprint = self._sprint(days=5, started_days_ago=2)
        report = sprint_burndown(self.scrum, sprint)
        dates = [row["date"] for row in report["days"]]
        self.assertEqual(dates[0], sprint.start_date)
        self.assertEqual(dates[-1], sprint.end_date)
        self.assertEqual([r["remaining"] for r in report["days"]][3:], [None, None])
        self.assertIsNotNone(report["days"][2]["remaining"])

    def test_counts_tasks_when_the_project_does_not_estimate(self):
        sprint = self._sprint(days=3, started_days_ago=2)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=sprint, status=self.todo
        )
        create_task(self.scrum, self.admin, title="b", sprint=sprint, status=self.todo)
        _shift(task, days=2)
        move_tasks(self.scrum, self.done, [task.uuid])
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual(report["unit"], "tasks")
        # Day 0: only task a existed and was open. Day 1: nothing changed.
        # Today: b joined, a is done.
        self.assertEqual([r["remaining"] for r in report["days"]], [1, 1, 1])
        self.assertEqual([r["scope"] for r in report["days"]], [1, 1, 2])
        self.assertEqual(report["remaining"], 1)
        self.assertEqual(report["scope"], 2)

    def test_sums_estimates_when_the_project_estimates(self):
        self._estimating()
        sprint = self._sprint(days=2, started_days_ago=1)
        create_task(
            self.scrum,
            self.admin,
            title="a",
            sprint=sprint,
            status=self.todo,
            estimate=Decimal("3"),
        )
        create_task(
            self.scrum,
            self.admin,
            title="b",
            sprint=sprint,
            status=self.todo,
            estimate=Decimal("2.5"),
        )
        unestimated = create_task(
            self.scrum, self.admin, title="c", sprint=sprint, status=self.todo
        )
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual(report["unit"], "points")
        self.assertEqual(report["remaining"], Decimal("5.5"))
        self.assertEqual(report["unestimated"], 1)
        self.assertIsNotNone(unestimated)

    def test_the_estimate_before_a_change_comes_from_the_change_event(self):
        self._estimating()
        sprint = self._sprint(days=3, started_days_ago=2)
        task = create_task(
            self.scrum,
            self.admin,
            title="a",
            sprint=sprint,
            status=self.todo,
            estimate=Decimal("8"),
        )
        _shift(task, days=2)
        task.estimate = Decimal("3")
        task.save(update_fields=["estimate"])
        TaskEvent.objects.create(
            project=self.scrum,
            task=task,
            task_title=task.title,
            task_number=task.number,
            type=TaskEvent.Type.ESTIMATED,
            from_value="8",
            to_value="3",
        )
        remaining = [
            r["remaining"] for r in sprint_burndown(self.scrum, sprint)["days"]
        ]
        self.assertEqual(remaining, [Decimal("8"), Decimal("8"), Decimal("3")])

    def test_a_task_carried_over_at_close_leaves_the_remaining_line(self):
        sprint = self._sprint(days=2, started_days_ago=1)
        create_task(self.scrum, self.admin, title="a", sprint=sprint, status=self.todo)
        complete_sprint(sprint, actor=self.admin)
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual(report["days"][-1]["remaining"], 0)
        self.assertEqual(report["days"][-1]["scope"], 0)

    def test_a_done_task_stays_in_scope_after_close(self):
        sprint = self._sprint(days=2, started_days_ago=1)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=sprint, status=self.todo
        )
        move_tasks(self.scrum, self.done, [task.uuid])
        complete_sprint(sprint, actor=self.admin)
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual(
            report["days"][-1], {**report["days"][-1], "remaining": 0, "scope": 1}
        )

    def test_ideal_line_falls_from_the_first_days_scope_to_zero(self):
        sprint = self._sprint(days=3, started_days_ago=2)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=sprint, status=self.todo
        )
        create_task(self.scrum, self.admin, title="b", sprint=sprint, status=self.todo)
        _shift(task, days=2)
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual([r["ideal"] for r in report["days"]], [1, 0.5, 0])
        self.assertEqual(report["initial_scope"], 1)

    def test_a_sprint_without_a_start_date_has_no_burndown(self):
        self.assertIsNone(sprint_burndown(self.scrum, self._sprint()))

    def test_a_running_sprint_without_an_end_date_is_drawn_up_to_today(self):
        sprint = self._sprint()
        start_sprint(sprint, actor=self.admin)
        sprint.start_date = self.today - timedelta(days=3)
        sprint.save(update_fields=["start_date"])
        report = sprint_burndown(self.scrum, sprint)
        self.assertEqual(len(report["days"]), 4)
        self.assertEqual(report["days"][-1]["date"], self.today)

    def test_tasks_of_another_sprint_do_not_count(self):
        sprint = self._sprint(started_days_ago=0)
        other = self.scrum.sprints.create(name="Sprint 2")
        create_task(self.scrum, self.admin, title="elsewhere", sprint=other)
        self.assertEqual(sprint_burndown(self.scrum, sprint)["scope"], 0)

    def test_a_task_moved_into_the_sprint_later_joins_that_day(self):
        sprint = self._sprint(days=3, started_days_ago=2)
        task = create_task(self.scrum, self.admin, title="late")
        _shift(task, days=2)
        assign_tasks_to_sprint(self.scrum, sprint, [task.uuid], actor=self.admin)
        self.assertEqual(
            [r["scope"] for r in sprint_burndown(self.scrum, sprint)["days"]], [0, 0, 1]
        )

    def test_a_renamed_sprint_keeps_its_history(self):
        sprint = self._sprint(days=2, started_days_ago=1)
        create_task(self.scrum, self.admin, title="a", sprint=sprint, status=self.todo)
        sprint.name = "Iteration 1"
        sprint.save(update_fields=["name"])
        self.assertEqual(sprint_burndown(self.scrum, sprint)["scope"], 1)

    def test_a_reused_name_does_not_inherit_the_former_sprints_tasks(self):
        former = self._sprint("Sprint 9", days=2, started_days_ago=1)
        create_task(
            self.scrum, self.admin, title="old", sprint=former, status=self.todo
        )
        former.delete()
        reborn = self._sprint("Sprint 9", days=2, started_days_ago=1)
        self.assertEqual(sprint_burndown(self.scrum, reborn)["scope"], 0)

    def test_a_task_deleted_before_any_estimate_change_keeps_its_points(self):
        self._estimating()
        sprint = self._sprint(days=3, started_days_ago=2)
        task = create_task(
            self.scrum,
            self.admin,
            title="a",
            sprint=sprint,
            status=self.todo,
            estimate=Decimal("5"),
        )
        _shift(task, days=2)
        delete_task(task, actor=self.admin)
        remaining = [
            r["remaining"] for r in sprint_burndown(self.scrum, sprint)["days"]
        ]
        self.assertEqual(remaining, [Decimal("5"), Decimal("5"), 0])


class SharedEventLogTests(HistoryTestCase):
    def test_one_log_serves_several_reports_with_the_same_answers(self):
        self._finished_task(created_days_ago=6, started_days_ago=3, done_days_ago=1)
        create_task(self.project, self.admin, title="Open", status=self.doing)
        log = EventLog(self.project)
        self.assertEqual(
            [s.lead for s in task_durations(self.project, log=log)],
            [s.lead for s in task_durations(self.project)],
        )
        self.assertEqual(
            cumulative_flow(self.project, days=7, log=log),
            cumulative_flow(self.project, days=7),
        )

    def test_each_replay_starts_from_the_beginning(self):
        create_task(self.project, self.admin, title="A")
        log = EventLog(self.project)
        self.assertEqual(
            cumulative_flow(self.project, days=2, log=log)[-1]["backlog"], 1
        )
        self.assertEqual(
            cumulative_flow(self.project, days=2, log=log)[-1]["backlog"], 1
        )

    def test_the_log_is_read_once(self):
        create_task(self.project, self.admin, title="A")
        log = EventLog(self.project)
        with self.assertNumQueries(0):
            cumulative_flow(self.project, days=3, log=log)
            task_durations(self.project, log=log)


class SprintVelocityTests(ScrumHistoryTestCase):
    def _closed_sprint(self, name, done_estimates, *, open_tasks=0, end_days_ago=0):
        sprint = self.scrum.sprints.create(
            name=name,
            state=Sprint.State.CLOSED,
            start_date=self.today - timedelta(days=end_days_ago + 7),
            end_date=self.today - timedelta(days=end_days_ago),
        )
        for estimate in done_estimates:
            create_task(
                self.scrum,
                self.admin,
                title="done",
                sprint=sprint,
                status=self.done,
                estimate=estimate,
            )
        for _ in range(open_tasks):
            create_task(self.scrum, self.admin, title="open", sprint=sprint)
        return sprint

    def test_counts_done_tasks_per_closed_sprint_oldest_first(self):
        self._closed_sprint("S2", [None, None, None], end_days_ago=7)
        self._closed_sprint("S1", [None], end_days_ago=14)
        rows = sprint_velocity(self.scrum)
        self.assertEqual([r["sprint"].name for r in rows], ["S1", "S2"])
        self.assertEqual([r["completed"] for r in rows], [1, 3])

    def test_open_tasks_do_not_count(self):
        self._closed_sprint("S1", [None], open_tasks=2)
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 1)

    def test_sums_points_when_the_project_estimates(self):
        self._estimating()
        self._closed_sprint("S1", [Decimal("3"), Decimal("2.5"), None])
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], Decimal("5.5"))

    def test_a_closed_sprint_with_nothing_done_reports_zero(self):
        self._closed_sprint("S1", [], open_tasks=1)
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 0)

    def test_rolling_average_covers_the_last_three_sprints(self):
        for index, n in enumerate((2, 4, 6, 8)):
            self._closed_sprint(f"S{index}", [None] * n, end_days_ago=(4 - index) * 7)
        self.assertEqual(
            [r["average"] for r in sprint_velocity(self.scrum)], [2, 3, 4, 6]
        )

    def test_open_sprints_are_not_velocity(self):
        self._closed_sprint("S1", [None])
        active = self._sprint("S2", started_days_ago=0)
        create_task(self.scrum, self.admin, title="x", sprint=active, status=self.done)
        self.assertEqual(len(sprint_velocity(self.scrum)), 1)

    def test_limit_keeps_the_most_recent_sprints(self):
        for index in range(4):
            self._closed_sprint(
                f"S{index}", [None] * (index + 1), end_days_ago=(4 - index) * 7
            )
        rows = sprint_velocity(self.scrum, limit=2)
        self.assertEqual([r["sprint"].name for r in rows], ["S2", "S3"])

    def test_a_finished_task_deleted_since_still_counts(self):
        sprint = self._closed_sprint("S1", [None, None])
        doomed = sprint.tasks.first()
        delete_task(doomed, actor=self.admin)
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 2)

    def test_a_task_reopened_after_the_close_still_counts_for_the_sprint(self):
        sprint = self._sprint("S1", days=2, started_days_ago=1)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=sprint, status=self.todo
        )
        move_tasks(self.scrum, self.done, [task.uuid])
        complete_sprint(sprint, actor=self.admin)
        move_tasks(self.scrum, self.todo, [task.uuid])
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 1)

    def test_work_finished_after_the_close_is_not_credited_to_the_sprint(self):
        sprint = self._sprint("S1", days=2, started_days_ago=1)
        task = create_task(
            self.scrum, self.admin, title="a", sprint=sprint, status=self.todo
        )
        complete_sprint(sprint, actor=self.admin)
        # Carried back to the pool at close; finishing it now belongs to no sprint.
        move_tasks(self.scrum, self.done, [task.uuid])
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 0)

    def test_a_sprint_closed_before_the_stamp_existed_is_read_as_it_stands(self):
        sprint = self._closed_sprint("S1", [None])
        self.assertIsNone(sprint.closed_at)
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 1)

    def test_sprints_are_ordered_by_their_close(self):
        late = self._sprint("Closed second", days=2, started_days_ago=1)
        complete_sprint(late, actor=self.admin)
        early = self._closed_sprint("Closed first", [None], end_days_ago=30)
        self.assertEqual(
            [r["sprint"].name for r in sprint_velocity(self.scrum)],
            ["Closed second", "Closed first"],
        )
        self.assertIsNotNone(early)

    def test_a_task_carried_over_at_close_counts_for_neither_sprint(self):
        first = self._sprint("S1", days=2, started_days_ago=1)
        task = create_task(
            self.scrum, self.admin, title="slow", sprint=first, status=self.todo
        )
        complete_sprint(first, actor=self.admin)
        self.assertEqual(sprint_velocity(self.scrum)[0]["completed"], 0)
        self.assertIsNotNone(task)

    def test_velocity_is_read_from_a_shared_log_without_new_queries(self):
        self._closed_sprint("S1", [None])
        log = EventLog(self.scrum)
        with self.assertNumQueries(1):
            # The one query lists the closed sprints; the log is already loaded.
            rows = sprint_velocity(self.scrum, log=log)
        self.assertEqual(rows[0]["completed"], 1)

    def test_summary_reads_the_last_sprint(self):
        self._closed_sprint("S1", [None], end_days_ago=7)
        self._closed_sprint("S2", [None, None, None])
        self.assertEqual(
            velocity_summary(sprint_velocity(self.scrum)),
            {"count": 2, "last": 3, "average": 2},
        )

    def test_summary_of_nothing(self):
        self.assertEqual(
            velocity_summary([]), {"count": 0, "last": None, "average": None}
        )
