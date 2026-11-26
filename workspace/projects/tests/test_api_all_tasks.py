from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

URL = "/api/v1/projects/tasks"


class AllTasksApiTests(ProjectTestMixin, APITestCase):
    """The list that spans every project the caller can open."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Fix the login form")
        self.other = create_project(self.admin, name="Mobile app")
        self.other_task = create_task(self.other, self.admin, title="Ship the login")

    def _titles(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [item["title"] for item in response.data]

    def test_lists_the_tasks_of_every_project_the_caller_can_open(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            sorted(self._titles()), ["Fix the login form", "Ship the login"]
        )
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(), ["Fix the login form"])
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self._titles(), [])

    def test_a_task_names_its_project(self):
        self.client.force_authenticate(self.member)
        (item,) = self.client.get(URL).data
        self.assertEqual(
            item["project"],
            {
                "uuid": str(self.project.uuid),
                "name": "Website",
                "key": self.project.key,
            },
        )
        self.assertEqual(item["reference"], self.task.reference)

    def test_reference_lookup_finds_the_task(self):
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(q=self.task.reference), ["Fix the login form"])

    def test_bare_number_lookup_finds_the_task(self):
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(q=f"#{self.task.number}"), ["Fix the login form"])

    def test_title_search_finds_the_task(self):
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(q="login"), ["Fix the login form"])

    def test_a_task_named_by_reference_comes_before_the_ones_quoting_it(self):
        quoting = create_task(
            self.project, self.admin, title=f"Follow-up on {self.task.reference}"
        )
        self.client.force_authenticate(self.member)
        self.assertEqual(
            self._titles(q=self.task.reference), ["Fix the login form", quoting.title]
        )

    def test_a_task_matching_by_reference_and_by_text_is_listed_once(self):
        self.task.title = f"Follow-up on {self.task.reference}"
        self.task.save(update_fields=["title"])
        self.client.force_authenticate(self.member)
        self.assertEqual(len(self._titles(q=self.task.reference)), 1)

    def test_a_search_never_reaches_a_project_out_of_reach(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self._titles(q="login"), [])
        self.assertEqual(self._titles(q=self.task.reference), [])

    def test_archived_projects_are_left_out_unless_asked_for(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(), [])
        self.assertEqual(self._titles(include_archived="false"), [])
        self.assertEqual(self._titles(include_archived="true"), ["Fix the login form"])

    def test_narrowed_to_a_project_by_uuid_or_by_key(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._titles(project=str(self.other.uuid)), ["Ship the login"])
        self.assertEqual(
            self._titles(project_key=self.other.key.lower()), ["Ship the login"]
        )
        self.assertEqual(self._titles(project_key="NOPE"), [])

    def test_a_project_out_of_reach_narrows_to_nothing(self):
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(project=str(self.other.uuid)), [])
        self.assertEqual(self._titles(project_key=self.other.key), [])

    def test_me_names_the_caller(self):
        add_member(self.other, self.member)
        self.task.assignees.add(self.member)
        self.other_task.assignees.add(self.admin)
        self.client.force_authenticate(self.member)
        self.assertEqual(self._titles(assignee="me"), ["Fix the login form"])
        self.assertEqual(
            sorted(self._titles(assignee="me,none")), ["Fix the login form"]
        )
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._titles(assignee="me"), ["Ship the login"])
        self.assertEqual(self._titles(created_by="me", assignee="none"), [])

    def test_relations_are_found_by_what_a_person_calls_them(self):
        self.task.assignees.add(self.member)
        done = self.project.statuses.get(name="Done")
        self.task.status = done
        self.task.save(update_fields=["status"])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._titles(status_name="done"), ["Fix the login form"])
        self.assertEqual(
            self._titles(status_name="DONE,nothing"), ["Fix the login form"]
        )
        self.assertEqual(self._titles(status_name="don"), [])
        self.assertEqual(
            self._titles(assignee_username="MEMBER1"), ["Fix the login form"]
        )
        self.assertEqual(self._titles(assignee_username="nobody"), [])
        self.assertEqual(
            self._titles(created_by_username="admin1", project_key=self.project.key),
            ["Fix the login form"],
        )

    def test_the_list_is_bounded_and_paged(self):
        for i in range(3):
            create_task(self.project, self.admin, title=f"extra {i}")
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"limit": 2})
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response["X-Has-More"], "true")
        rest = self.client.get(URL, {"limit": 2, "offset": 2})
        self.assertEqual(len(rest.data), 2)
        self.assertEqual(rest["X-Has-More"], "false")

    def test_an_unreadable_value_names_its_parameter(self):
        self.client.force_authenticate(self.member)
        for params in (
            {"project": "not-a-uuid"},
            {"assignee": "someone"},
            {"include_archived": "maybe"},
            {"ordering": "title"},
            {"limit": "0"},
        ):
            with self.subTest(params=params):
                response = self.client.get(URL, params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(response.data), list(params))

    def test_anonymous_request_is_rejected(self):
        response = self.client.get(URL, {"q": "login"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
