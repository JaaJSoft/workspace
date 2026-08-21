from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Task, TaskEvent
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class TaskApiMixin(ProjectTestMixin):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.tasks_url = f"/api/v1/projects/{self.project.uuid}/tasks"


class TaskListCreateTests(TaskApiMixin, APITestCase):
    def test_member_creates_task_defaulting_to_backlog(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.tasks_url, {"title": "Ship it"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], self.backlog.uuid)
        self.assertEqual(response.data["status_category"], "backlog")

    def test_create_with_assignee_and_label(self):
        label = self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {
                "title": "Fix",
                "status": str(self.todo.uuid),
                "assignees": [str(self.admin.pk)],
                "labels": [str(label.uuid)],
                "priority": "high",
                "due_date": "2026-08-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task = Task.objects.get(uuid=response.data["uuid"])
        self.assertEqual(list(task.assignees.all()), [self.admin])
        self.assertEqual(task.status, self.todo)

    def test_assignee_must_be_project_member(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "Fix", "assignees": [str(self.outsider.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_status_from_another_project_rejected(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        foreign_status = other.statuses.first()
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "Fix", "status": str(foreign_status.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filters(self):
        t1 = create_task(self.project, self.admin, title="alpha")
        create_task(self.project, self.admin, title="beta", status=self.todo)
        t1.assignees.add(self.member)
        self.client.force_authenticate(self.member)

        response = self.client.get(self.tasks_url, {"status": str(self.todo.uuid)})
        self.assertEqual([t["title"] for t in response.data], ["beta"])

        response = self.client.get(self.tasks_url, {"assignee": str(self.member.pk)})
        self.assertEqual([t["title"] for t in response.data], ["alpha"])

        response = self.client.get(self.tasks_url, {"q": "alpha"})
        self.assertEqual([t["title"] for t in response.data], ["alpha"])

    def test_task_search_matches_description(self):
        # `?q=` used to be a title-only icontains; full-text search must
        # also match words that appear only in the description.
        task = create_task(self.project, self.admin, title="Quarterly review")
        task.description = "prepare the pelican slides"
        task.save(update_fields=["description"])
        self.client.force_authenticate(self.member)
        response = self.client.get(self.tasks_url, {"q": "pelican"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(str(task.uuid), [t["uuid"] for t in response.data])

    def test_malformed_filter_uuid_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.tasks_url, {"status": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_rejects_creation(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(self.tasks_url, {"title": "Nope"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskListFilteringTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.bug = self.project.labels.create(name="bug", color="#ff0000")
        self.ui = self.project.labels.create(name="ui", color="#00ff00")
        self.alpha = create_task(
            self.project,
            self.admin,
            title="alpha",
            priority="high",
            due_date=date(2026, 8, 10),
            assignees=[self.member],
            labels=[self.bug],
        )
        self.beta = create_task(
            self.project,
            self.member,
            title="beta",
            status=self.todo,
            priority="low",
            due_date=date(2026, 8, 20),
            labels=[self.ui],
        )
        self.gamma = create_task(
            self.project,
            self.admin,
            title="gamma",
            status=self.done,
            priority="urgent",
        )
        self.client.force_authenticate(self.member)

    def _titles(self, params):
        response = self.client.get(self.tasks_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [t["title"] for t in response.data]

    def test_multi_value_status_ors_the_columns(self):
        titles = self._titles({"status": [str(self.todo.uuid), str(self.done.uuid)]})
        self.assertEqual(sorted(titles), ["beta", "gamma"])

    def test_multi_value_label_ors_without_duplicating_rows(self):
        self.alpha.labels.add(self.ui)
        titles = self._titles({"label": [str(self.bug.uuid), str(self.ui.uuid)]})
        self.assertEqual(sorted(titles), ["alpha", "beta"])

    def test_assignee_none_matches_unassigned_tasks(self):
        titles = self._titles({"assignee": "none"})
        self.assertEqual(sorted(titles), ["beta", "gamma"])

    def test_assignee_mixes_ids_and_none(self):
        titles = self._titles({"assignee": [str(self.member.pk), "none"]})
        self.assertEqual(sorted(titles), ["alpha", "beta", "gamma"])

    def test_priority_filter(self):
        self.assertEqual(self._titles({"priority": "high"}), ["alpha"])
        response = self.client.get(self.tasks_url, {"priority": "blocker"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_due_date_range_bounds_are_inclusive(self):
        self.assertEqual(self._titles({"due_before": "2026-08-10"}), ["alpha"])
        self.assertEqual(self._titles({"due_after": "2026-08-20"}), ["beta"])
        self.assertEqual(
            sorted(
                self._titles({"due_after": "2026-08-10", "due_before": "2026-08-20"})
            ),
            ["alpha", "beta"],
        )

    def test_malformed_and_impossible_dates_are_400(self):
        for value in ("not-a-date", "2026-13-01"):
            response = self.client.get(self.tasks_url, {"due_before": value})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_created_by_filter(self):
        self.assertEqual(self._titles({"created_by": str(self.member.pk)}), ["beta"])
        response = self.client.get(self.tasks_url, {"created_by": "abc"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completed_filter_follows_the_status_category(self):
        self.assertEqual(self._titles({"completed": "true"}), ["gamma"])
        self.assertEqual(
            sorted(self._titles({"completed": "false"})), ["alpha", "beta"]
        )
        # is_truthy is permissive: an unknown value reads as false, not 400.
        self.assertEqual(
            sorted(self._titles({"completed": "maybe"})), ["alpha", "beta"]
        )

    def test_filters_compose(self):
        titles = self._titles({"assignee": "none", "priority": "low"})
        self.assertEqual(titles, ["beta"])


class TaskListOrderingTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.low = create_task(self.project, self.admin, title="low", priority="low")
        self.urgent = create_task(
            self.project,
            self.admin,
            title="urgent",
            priority="urgent",
            due_date=date(2026, 8, 20),
        )
        self.medium = create_task(
            self.project,
            self.admin,
            title="medium",
            priority="medium",
            due_date=date(2026, 8, 10),
        )
        self.client.force_authenticate(self.member)

    def _titles(self, params):
        response = self.client.get(self.tasks_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [t["title"] for t in response.data]

    def test_priority_ordering_puts_most_important_first(self):
        self.assertEqual(
            self._titles({"ordering": "priority"}), ["urgent", "medium", "low"]
        )
        self.assertEqual(
            self._titles({"ordering": "-priority"}), ["low", "medium", "urgent"]
        )

    def test_due_date_ordering_sorts_tasks_without_a_due_date_last(self):
        self.assertEqual(
            self._titles({"ordering": "due_date"}), ["medium", "urgent", "low"]
        )
        self.assertEqual(
            self._titles({"ordering": "-due_date"}), ["urgent", "medium", "low"]
        )

    def test_created_at_ordering(self):
        self.assertEqual(
            self._titles({"ordering": "-created_at"}), ["medium", "urgent", "low"]
        )

    def test_unknown_ordering_field_is_400(self):
        response = self.client.get(self.tasks_url, {"ordering": "title"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ordering_composes_with_filters_and_pagination(self):
        response = self.client.get(
            self.tasks_url,
            {"ordering": "priority", "completed": "false", "limit": "2"},
        )
        self.assertEqual([t["title"] for t in response.data], ["urgent", "medium"])
        self.assertEqual(response.headers["X-Has-More"], "true")


class TaskListPaginationTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        for i in range(5):
            create_task(self.project, self.admin, title=f"task {i}")
        self.client.force_authenticate(self.member)

    def test_without_limit_the_full_array_is_returned(self):
        response = self.client.get(self.tasks_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 5)
        self.assertNotIn("X-Has-More", response.headers)

    def test_limit_returns_a_bare_array_page_with_has_more_header(self):
        response = self.client.get(self.tasks_url, {"limit": "2"})
        self.assertIsInstance(response.data, list)
        self.assertEqual([t["title"] for t in response.data], ["task 0", "task 1"])
        self.assertEqual(response.headers["X-Has-More"], "true")

    def test_offset_pages_through_to_the_end(self):
        response = self.client.get(self.tasks_url, {"limit": "2", "offset": "4"})
        self.assertEqual([t["title"] for t in response.data], ["task 4"])
        self.assertEqual(response.headers["X-Has-More"], "false")

    def test_exact_boundary_does_not_claim_more(self):
        response = self.client.get(self.tasks_url, {"limit": "5"})
        self.assertEqual(len(response.data), 5)
        self.assertEqual(response.headers["X-Has-More"], "false")

    def test_pagination_composes_with_filters(self):
        response = self.client.get(
            self.tasks_url, {"status": str(self.backlog.uuid), "limit": "3"}
        )
        self.assertEqual(len(response.data), 3)
        self.assertEqual(response.headers["X-Has-More"], "true")


class TaskDetailTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")
        self.detail_url = f"{self.tasks_url}/{self.task.uuid}"

    def test_member_updates_fields(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.detail_url, {"title": "renamed", "priority": "urgent"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "renamed")

    def test_patching_status_to_done_sets_completed_at(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.detail_url, {"status": str(self.done.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.completed_at)

    def test_member_deletes_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Task.objects.filter(uuid=self.task.uuid).exists())

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_payload_includes_number_and_reference(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.detail_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["number"], self.task.number)
        self.assertEqual(
            resp.data["reference"], f"{self.project.key}-{self.task.number}"
        )


class TaskUpdateEventTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(
            self.project, self.admin, title="Original", status=self.todo
        )
        self.task_url = f"{self.tasks_url}/{self.task.uuid}"

    def _updated_events(self):
        return TaskEvent.objects.filter(task=self.task, type=TaskEvent.Type.UPDATED)

    def test_field_edit_records_updated_event(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(self.task_url, {"title": "Renamed"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = self._updated_events().get()
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.task_title, "Renamed")

    def test_status_only_change_records_move_not_update(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.task_url, {"status": str(self.done.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._updated_events().count(), 0)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.COMPLETED
            ).exists()
        )

    def test_noop_edit_records_nothing(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.task_url, {"title": "Original"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._updated_events().count(), 0)

    def test_m2m_edit_records_updated_event(self):
        label = self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.task_url, {"labels": [str(label.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._updated_events().count(), 1)

    def test_assignee_addition_records_assigned_not_updated(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.task_url, {"assignees": [str(self.admin.pk)]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._updated_events().count(), 0)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.ASSIGNED
            ).exists()
        )

    def test_same_m2m_set_records_nothing(self):
        self.task.assignees.set([self.admin])
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.task_url, {"assignees": [str(self.admin.pk)]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._updated_events().count(), 0)


class TaskEstimateApiTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.project.estimate_unit = "points"
        self.project.save(update_fields=["estimate_unit"])

    def test_create_with_estimate(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url, {"title": "Sized", "estimate": "3.5"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["estimate"], "3.5")
        task = Task.objects.get(uuid=response.data["uuid"])
        self.assertEqual(task.estimate, Decimal("3.5"))

    def test_estimate_change_records_a_dedicated_event(self):
        task = create_task(
            self.project, self.admin, title="Sized", estimate=Decimal("3")
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}", {"estimate": "5"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = task.events.get(type=TaskEvent.Type.ESTIMATED)
        self.assertEqual(event.from_value, "3")
        self.assertEqual(event.to_value, "5")
        # No generic UPDATED noise on top: the estimate has its own event.
        self.assertFalse(task.events.filter(type=TaskEvent.Type.UPDATED).exists())

    def test_equal_estimate_records_no_event(self):
        task = create_task(
            self.project, self.admin, title="Sized", estimate=Decimal("3.0")
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}", {"estimate": "3"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(task.events.filter(type=TaskEvent.Type.ESTIMATED).exists())

    def test_clearing_the_estimate_snapshots_an_empty_to_value(self):
        task = create_task(
            self.project, self.admin, title="Sized", estimate=Decimal("2")
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}", {"estimate": None}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertIsNone(task.estimate)
        event = task.events.get(type=TaskEvent.Type.ESTIMATED)
        self.assertEqual(event.from_value, "2")
        self.assertEqual(event.to_value, "")

    def test_invalid_estimates_are_rejected(self):
        task = create_task(self.project, self.admin, title="Sized")
        self.client.force_authenticate(self.member)
        for bad in ("-1", "abc", "3.55", "123456"):
            response = self.client.patch(
                f"{self.tasks_url}/{task.uuid}", {"estimate": bad}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad)

    def test_estimate_ordering_sorts_unestimated_last(self):
        create_task(self.project, self.admin, title="five", estimate=Decimal("5"))
        create_task(self.project, self.admin, title="one", estimate=Decimal("1"))
        create_task(self.project, self.admin, title="none")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.tasks_url, {"ordering": "estimate"})
        self.assertEqual([t["title"] for t in response.data], ["one", "five", "none"])
        response = self.client.get(self.tasks_url, {"ordering": "-estimate"})
        self.assertEqual([t["title"] for t in response.data], ["five", "one", "none"])
