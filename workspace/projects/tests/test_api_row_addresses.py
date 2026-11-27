"""Labels, epics and sprints are addressed without their project
(``/projects/labels/{uuid}``): the row says which project it is in."""

import uuid as uuid_module

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project, Sprint
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

COLLECTIONS = ("labels", "epics", "sprints")


class RowAddressTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        self.scrum.members.create(user=self.member)
        self.rows = {
            "labels": self.scrum.labels.create(name="bug"),
            "epics": self.scrum.epics.create(name="Launch"),
            "sprints": self.scrum.sprints.create(name="Sprint 1"),
        }

    def _url(self, collection, uuid=None):
        return f"/api/v1/projects/{collection}/{uuid or self.rows[collection].pk}"

    def test_a_member_reads_one_row(self):
        self.client.force_authenticate(self.member)
        for collection in COLLECTIONS:
            with self.subTest(collection):
                response = self.client.get(self._url(collection))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["uuid"], str(self.rows[collection].pk))

    def test_one_row_carries_its_progress(self):
        done = self.scrum.statuses.get(category="done")
        for status_ in (done, None):
            create_task(
                self.scrum,
                self.admin,
                title="t",
                epic=self.rows["epics"],
                sprint=self.rows["sprints"],
                **({"status": status_} if status_ else {}),
            )
        self.client.force_authenticate(self.member)
        for collection in ("epics", "sprints"):
            with self.subTest(collection):
                response = self.client.get(self._url(collection))
                self.assertEqual(
                    (response.data["task_count"], response.data["done_task_count"]),
                    (2, 1),
                )

    def test_an_update_answers_the_row_with_its_progress(self):
        create_task(self.scrum, self.admin, title="t", epic=self.rows["epics"])
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self._url("epics"), {"name": "Relaunch"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Relaunch")
        self.assertEqual(response.data["task_count"], 1)

    def test_a_member_cannot_write_a_row(self):
        self.client.force_authenticate(self.member)
        for collection in COLLECTIONS:
            with self.subTest(collection):
                changed = self.client.patch(
                    self._url(collection), {"name": "Mine"}, format="json"
                )
                deleted = self.client.delete(self._url(collection))
                self.assertEqual(changed.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(deleted.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_row_out_of_reach_answers_like_one_that_does_not_exist(self):
        self.client.force_authenticate(self.outsider)
        for collection in COLLECTIONS:
            for method in ("get", "patch", "delete"):
                with self.subTest(collection=collection, method=method):
                    hidden = getattr(self.client, method)(self._url(collection))
                    unknown = getattr(self.client, method)(
                        self._url(collection, uuid_module.uuid4())
                    )
                    self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                    self.assertEqual(
                        (hidden.status_code, hidden.content),
                        (unknown.status_code, unknown.content),
                    )
        self.assertEqual(self.scrum.labels.count(), 1)

    def test_a_sprint_out_of_reach_cannot_be_started_or_completed(self):
        self.client.force_authenticate(self.outsider)
        for verb in ("start", "complete"):
            with self.subTest(verb):
                hidden = self.client.post(f"{self._url('sprints')}/{verb}")
                unknown = self.client.post(
                    f"{self._url('sprints', uuid_module.uuid4())}/{verb}"
                )
                self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (unknown.status_code, unknown.content),
                )
        self.rows["sprints"].refresh_from_db()
        self.assertEqual(self.rows["sprints"].state, Sprint.State.PLANNED)

    def test_completing_into_a_sprint_of_another_project_answers_like_an_unknown_one(
        self,
    ):
        sprint = self.rows["sprints"]
        sprint.state = Sprint.State.ACTIVE
        sprint.save(update_fields=["state"])
        other = create_project(
            self.outsider, name="Elsewhere", project_type=Project.Type.SCRUM
        )
        foreign = other.sprints.create(name="Sprint X")
        self.client.force_authenticate(self.admin)

        def complete_into(target):
            response = self.client.post(
                f"{self._url('sprints')}/complete",
                {"move_to": str(target)},
                format="json",
            )
            return response.status_code, response.content.replace(
                str(target).encode(), b"<uuid>"
            )

        refused = complete_into(foreign.pk)
        self.assertEqual(refused[0], status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused, complete_into(uuid_module.uuid4()))
        sprint.refresh_from_db()
        self.assertEqual(sprint.state, Sprint.State.ACTIVE)
