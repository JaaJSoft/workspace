from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project
from workspace.projects.services.projects import create_project

from .base import ProjectTestMixin


class ProjectListFilterTests(ProjectTestMixin, APITestCase):
    """``GET /projects``: the caller's projects, narrowed and sorted the way
    every list of the API is."""

    def setUp(self):
        super().setUp()
        self.rocket = create_project(
            self.admin,
            name="Rocket",
            description="Réunion de lancement",
            project_type=Project.Type.SCRUM,
        )
        self.old = create_project(self.admin, name="Old site")
        self.old.archived_at = timezone.now()
        self.old.save(update_fields=["archived_at"])
        self.devs = Group.objects.create(name="Dévs")
        self.rocket.groups.add(self.devs)
        create_project(self.outsider, name="Elsewhere")
        self.client.force_authenticate(self.admin)

    def _names(self, **params):
        response = self.client.get("/api/v1/projects", params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [project["name"] for project in response.data]

    def test_lists_every_project_of_the_caller_by_name(self):
        self.assertEqual(self._names(), ["Old site", "Rocket", "Website"])

    def test_q_searches_name_key_and_description_whatever_the_accents(self):
        self.assertEqual(self._names(q="reunion"), ["Rocket"])
        self.assertEqual(self._names(q="site"), ["Old site", "Website"])
        self.assertEqual(self._names(q=self.project.key.lower()), ["Website"])

    def test_archived_has_three_states(self):
        self.assertEqual(self._names(archived="true"), ["Old site"])
        self.assertEqual(self._names(archived="false"), ["Rocket", "Website"])

    def test_type_takes_several_values(self):
        self.assertEqual(self._names(type="scrum"), ["Rocket"])
        self.assertEqual(
            self._names(type="scrum,kanban"), ["Old site", "Rocket", "Website"]
        )

    def test_group_by_id_and_by_name(self):
        self.assertEqual(self._names(group=self.devs.pk), ["Rocket"])
        self.assertEqual(self._names(group_name="devs"), ["Rocket"])
        self.assertEqual(self._names(group_name="nobody"), [])

    def test_ordering_on_a_declared_field(self):
        self.assertEqual(
            self._names(ordering="-name"), ["Website", "Rocket", "Old site"]
        )
        self.assertEqual(
            self._names(ordering="created_at"), ["Website", "Rocket", "Old site"]
        )

    def test_a_value_that_cannot_be_read_names_its_parameter(self):
        for parameter, value in [
            ("archived", "maybe"),
            ("type", "waterfall"),
            ("group", "devs"),
            ("ordering", "owner"),
            ("limit", "0"),
        ]:
            with self.subTest(parameter=parameter):
                response = self.client.get("/api/v1/projects", {parameter: value})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(response.data), [parameter])

    def test_pages_with_limit_and_offset(self):
        first = self.client.get("/api/v1/projects", {"limit": 2})
        self.assertEqual([p["name"] for p in first.data], ["Old site", "Rocket"])
        self.assertEqual(first["X-Has-More"], "true")
        rest = self.client.get("/api/v1/projects", {"limit": 2, "offset": 2})
        self.assertEqual([p["name"] for p in rest.data], ["Website"])
        self.assertEqual(rest["X-Has-More"], "false")

    def test_count_is_sent_on_request(self):
        response = self.client.get("/api/v1/projects", {"count": "true", "limit": 1})
        self.assertEqual(response["X-Total-Count"], "3")

    def test_groups_are_small_objects(self):
        response = self.client.get("/api/v1/projects", {"q": "rocket"})
        self.assertEqual(
            response.data[0]["groups"], [{"id": self.devs.pk, "name": "Dévs"}]
        )
        self.assertFalse(response.data[0]["is_archived"])
