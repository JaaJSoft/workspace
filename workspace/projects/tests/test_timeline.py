from datetime import date, timedelta

from django.test import TestCase

from workspace.projects.models import Milestone, Project, Sprint
from workspace.projects.services.milestones import milestones_with_progress
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task
from workspace.projects.services.timeline import (
    DEFAULT_GROUPING,
    DEFAULT_SCALE,
    build_timeline,
    coerce_grouping,
    coerce_scale,
)

from .base import ProjectTestMixin

TODAY = date(2026, 9, 12)


class CoerceTests(TestCase):
    def test_known_values_pass_and_unknown_fall_back(self):
        self.assertEqual(coerce_scale("quarter"), "quarter")
        self.assertEqual(coerce_scale("decade"), DEFAULT_SCALE)
        self.assertEqual(coerce_scale(None), DEFAULT_SCALE)
        self.assertEqual(coerce_grouping("epic"), "epic")
        self.assertEqual(coerce_grouping(""), DEFAULT_GROUPING)


class BuildTimelineTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.done = self.project.statuses.get(name="Done")
        self.todo = self.project.statuses.get(name="To do")
        self.beta = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        self.ga = Milestone.objects.create(
            project=self.project, name="GA", target_date=date(2026, 12, 1)
        )

    def _task(
        self, title, *, start=None, due=None, milestone=None, status=None, epic=None
    ):
        task = create_task(
            self.project,
            self.admin,
            title=title,
            start_date=start,
            due_date=due,
            milestone=milestone,
            status=status,
            epic=epic,
        )
        return task

    def _build(self, tasks, *, group="milestone", scale="week", sprints=()):
        return build_timeline(
            tasks,
            list(milestones_with_progress(self.project)),
            list(sprints),
            group=group,
            scale=scale,
            today=TODAY,
        )

    def _tasks(self):
        return list(self.project.tasks.select_related("status", "epic", "milestone"))

    def test_groups_milestones_by_date_then_no_milestone(self):
        self._task("late", due=date(2026, 11, 20), milestone=self.ga)
        self._task("early", due=date(2026, 9, 20), milestone=self.beta)
        self._task("loose", due=date(2026, 9, 25))
        result = self._build(self._tasks())
        self.assertEqual(
            [g["label"] for g in result["groups"]], ["Beta", "GA", "No milestone"]
        )
        self.assertEqual(result["groups"][0]["progress"], "0/1")
        self.assertEqual(result["groups"][0]["sublabel"], "Oct 01")
        self.assertEqual(result["groups"][2]["progress"], "")

    def test_empty_milestone_keeps_its_group_but_empty_no_milestone_is_dropped(self):
        self._task("early", due=date(2026, 9, 20), milestone=self.beta)
        result = self._build(self._tasks())
        self.assertEqual([g["label"] for g in result["groups"]], ["Beta", "GA"])
        self.assertEqual(result["groups"][1]["rows"], [])

    def test_groups_by_epic_in_name_order(self):
        zed = self.project.epics.create(name="Zed")
        alpha = self.project.epics.create(name="Alpha")
        self._task("z", due=date(2026, 9, 20), epic=zed)
        self._task("a", due=date(2026, 9, 21), epic=alpha)
        self._task("none", due=date(2026, 9, 22))
        result = self._build(self._tasks(), group="epic")
        self.assertEqual(
            [g["label"] for g in result["groups"]], ["Alpha", "Zed", "No epic"]
        )
        self.assertTrue(all(g["progress"] == "" for g in result["groups"]))

    def test_placement_kinds(self):
        self._task("bar", start=date(2026, 9, 1), due=date(2026, 9, 5))
        self._task("due", due=date(2026, 9, 20))
        self._task("start", start=date(2026, 9, 21))
        self._task("undated")
        # Beta and GA come first (empty); the loose tasks sit in the trailing
        # "No milestone" group.
        result = self._build(self._tasks())
        rows = {r["label"].split(" ", 1)[1]: r for r in result["groups"][-1]["rows"]}
        self.assertEqual(rows["bar"]["kind"], "bar")
        self.assertEqual(
            (rows["bar"]["start"], rows["bar"]["end"]),
            (date(2026, 9, 1), date(2026, 9, 5)),
        )
        self.assertEqual(rows["due"]["kind"], "marker")
        self.assertEqual(rows["due"]["start"], date(2026, 9, 20))
        self.assertEqual(rows["start"]["kind"], "marker")
        self.assertEqual(rows["start"]["start"], date(2026, 9, 21))
        self.assertNotIn("undated", rows)
        self.assertEqual(result["undated_count"], 1)

    def test_rows_are_ordered_by_first_date(self):
        self._task("second", due=date(2026, 9, 20))
        self._task("first", start=date(2026, 9, 10), due=date(2026, 9, 30))
        result = self._build(self._tasks())
        labels = [r["label"] for r in result["groups"][-1]["rows"]]
        self.assertTrue(labels[0].endswith("first"))

    def test_css_class_by_category_and_overdue(self):
        self._task("done", due=date(2026, 9, 1), status=self.done)
        self._task("active", due=date(2026, 9, 20), status=self.todo)
        self._task("backlog", due=date(2026, 9, 20))
        self._task("late", due=date(2026, 9, 1), status=self.todo)
        result = self._build(self._tasks())
        classes = {
            r["label"].split(" ", 1)[1]: r["css_class"]
            for r in result["groups"][-1]["rows"]
        }
        self.assertEqual(classes["done"], "fill-success")
        self.assertEqual(classes["active"], "fill-accent")
        self.assertEqual(classes["backlog"], "fill-neutral")
        self.assertEqual(classes["late"], "fill-error")

    def test_row_label_and_tooltip_carry_the_reference(self):
        task = self._task("Build it", due=date(2026, 9, 20))
        row = self._build(self._tasks())["groups"][-1]["rows"][0]
        self.assertEqual(row["id"], str(task.uuid))
        self.assertEqual(row["label"], f"{self.project.key}-{task.number} Build it")
        self.assertIn("due Sep 20", row["tooltip"])

    def test_extent_pads_by_one_unit_and_includes_today(self):
        self._task("t", due=date(2026, 9, 20))
        week = self._build(self._tasks(), scale="week")
        self.assertEqual(
            (week["start"], week["end"]), (TODAY - timedelta(days=7), date(2026, 12, 8))
        )
        quarter = self._build(self._tasks(), scale="quarter")
        self.assertEqual(quarter["start"], TODAY - timedelta(days=90))

    def test_extent_is_clamped_and_far_rows_are_counted(self):
        self._task("near", due=date(2026, 9, 20))
        self._task("far", due=date(2031, 1, 1))
        result = self._build(self._tasks())
        self.assertEqual(result["end"], TODAY + timedelta(days=548))
        self.assertEqual(result["clipped_count"], 1)
        self.assertEqual(len(result["groups"][-1]["rows"]), 1)

    def test_extent_without_data_is_one_unit_around_today(self):
        self.beta.delete()
        self.ga.delete()
        result = self._build([])
        self.assertEqual(
            (result["start"], result["end"]),
            (TODAY - timedelta(days=7), TODAY + timedelta(days=7)),
        )

    def test_markers_list_every_milestone_regardless_of_grouping(self):
        self.ga.is_closed = True
        self.ga.save(update_fields=["closed_at"])
        result = self._build([], group="epic")
        self.assertEqual([m["label"] for m in result["markers"]], ["Beta", "GA"])
        self.assertEqual(result["markers"][0]["css_class"], "fill-warning")
        self.assertEqual(result["markers"][1]["css_class"], "fill-base-content/30")

    def test_sprint_bands_need_both_dates(self):
        scrum = create_project(self.admin, name="S", project_type=Project.Type.SCRUM)
        dated = Sprint.objects.create(
            project=scrum,
            name="S1",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 20),
        )
        Sprint.objects.create(project=scrum, name="S2", start_date=date(2026, 9, 21))
        result = build_timeline(
            [],
            [],
            list(scrum.sprints.all()),
            group="milestone",
            scale="week",
            today=TODAY,
        )
        self.assertEqual(len(result["bands"]), 1)
        self.assertEqual(result["bands"][0]["label"], dated.name)
        self.assertEqual(result["bands"][0]["css_class"], "fill-info/10")
