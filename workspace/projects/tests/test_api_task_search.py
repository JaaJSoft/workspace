from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

URL = "/api/v1/projects/tasks/search"


class TaskSearchApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Fix the login form")

    def test_reference_lookup_finds_the_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": self.task.reference})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        (item,) = response.data
        self.assertEqual(item["uuid"], str(self.task.uuid))
        self.assertEqual(item["reference"], self.task.reference)
        self.assertEqual(item["project_name"], "Website")

    def test_bare_number_lookup_finds_the_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": f"#{self.task.number}"})
        self.assertEqual(
            [item["uuid"] for item in response.data], [str(self.task.uuid)]
        )

    def test_title_search_finds_the_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": "login"})
        self.assertIn(str(self.task.uuid), [item["uuid"] for item in response.data])

    def test_results_are_access_filtered(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(URL, {"q": "login"})
        self.assertEqual(response.data, [])

    def test_archived_projects_are_excluded(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": "login"})
        self.assertEqual(response.data, [])

    def test_exclude_param_drops_the_anchor_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": "login", "exclude": str(self.task.uuid)})
        self.assertEqual(response.data, [])

    def test_malformed_exclude_is_a_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": "login", "exclude": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_query_returns_nothing(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": "   "})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_reference_and_fulltext_matches_are_deduplicated(self):
        # A title that repeats the task's own reference makes both the
        # reference pass and the full-text pass return it.
        self.task.title = f"Follow-up on {self.task.reference}"
        self.task.save(update_fields=["title"])
        self.client.force_authenticate(self.member)
        response = self.client.get(URL, {"q": self.task.reference})
        uuids = [item["uuid"] for item in response.data]
        self.assertEqual(uuids.count(str(self.task.uuid)), 1)

    def test_anonymous_request_is_rejected(self):
        response = self.client.get(URL, {"q": "login"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
