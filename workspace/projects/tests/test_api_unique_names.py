"""A name another writer took between validation and the write answers a 400.

``validate_name`` is bypassed to stand for the race: the serializer saw the
name free, the unique constraint did not.
"""

from unittest.mock import patch

from rest_framework.test import APITestCase

from workspace.projects.models import Project
from workspace.projects.services.projects import create_project

from .base import ProjectTestMixin

CASES = [
    # (collection, serializer, message, scrum project)
    (
        "labels",
        "LabelSerializer",
        "A label with this name already exists in this project.",
        False,
    ),
    (
        "epics",
        "EpicSerializer",
        "An epic with this name already exists in this project.",
        False,
    ),
    (
        "sprints",
        "SprintSerializer",
        "A sprint with this name already exists in this project.",
        True,
    ),
    (
        "statuses",
        "TaskStatusSerializer",
        "A column with this name already exists in this project.",
        False,
    ),
]


class DuplicateNameRaceTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        self.client.force_authenticate(self.admin)

    def _create(self, project, collection, name):
        body = {"name": name}
        if collection == "statuses":
            body["category"] = "active"
        return self.client.post(
            f"/api/v1/projects/{project.uuid}/{collection}", body, format="json"
        )

    def _without_validation(self, serializer):
        return patch(
            f"workspace.projects.serializers.{serializer}.validate_name",
            side_effect=lambda value: value,
        )

    def test_create_with_a_taken_name(self):
        for collection, serializer, message, scrum in CASES:
            with self.subTest(collection):
                project = self.scrum if scrum else self.project
                self.assertEqual(
                    self._create(project, collection, "Taken").status_code, 201
                )
                with self._without_validation(serializer):
                    response = self._create(project, collection, "Taken")
                self.assertEqual(
                    (response.status_code, response.data), (400, {"name": [message]})
                )

    def test_rename_to_a_taken_name(self):
        for collection, serializer, message, scrum in CASES:
            with self.subTest(collection):
                project = self.scrum if scrum else self.project
                self.assertEqual(
                    self._create(project, collection, "Kept").status_code, 201
                )
                other = self._create(project, collection, "Renamed").data["uuid"]
                with self._without_validation(serializer):
                    response = self.client.patch(
                        f"/api/v1/projects/{project.uuid}/{collection}/{other}",
                        {"name": "Kept"},
                        format="json",
                    )
                self.assertEqual(
                    (response.status_code, response.data), (400, {"name": [message]})
                )
