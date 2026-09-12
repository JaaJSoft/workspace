"""Task file links API: link workspace files, list per viewer, unlink."""

import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.projects.models import TaskEvent, TaskFileLink
from workspace.projects.services.file_links import link_files
from workspace.projects.services.tasks import create_task
from workspace.projects.tests.base import ProjectTestMixin


class TaskFileLinkApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.url = f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/files"

    def _make_file(self, owner, name="doc.txt", content=b"hello"):
        return FileService.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, content, content_type="text/plain"),
        )

    def _link(self, user, *files):
        self.client.force_authenticate(user)
        return self.client.post(
            self.url,
            data={"file_uuids": [str(f.uuid) for f in files]},
            format="json",
        )

    # ── Create ────────────────────────────────────────────

    def test_member_links_an_accessible_file_and_gets_the_list(self):
        src = self._make_file(self.member)
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        (item,) = resp.data["files"]
        self.assertEqual(item["file_uuid"], str(src.uuid))
        self.assertEqual(item["name"], "doc.txt")
        link = TaskFileLink.objects.get()
        self.assertEqual(link.file, src)
        self.assertEqual(link.created_by, self.member)
        self.assertTrue(
            self.task.events.filter(
                type=TaskEvent.Type.FILE_LINKED, actor=self.member, to_value="doc.txt"
            ).exists()
        )

    def test_linking_an_inaccessible_file_is_rejected_whole(self):
        mine = self._make_file(self.member, "mine.txt")
        theirs = self._make_file(self.admin, "theirs.txt")
        resp = self._link(self.member, mine, theirs)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TaskFileLink.objects.exists())

    def test_unknown_uuid_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url, data={"file_uuids": [str(uuid.uuid4())]}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_request_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(self.url, data={"file_uuids": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_relinking_the_same_file_is_idempotent(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TaskFileLink.objects.count(), 1)

    def test_outsider_gets_404(self):
        src = self._make_file(self.outsider)
        resp = self._link(self.outsider, src)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_archived_project_is_read_only(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        src = self._make_file(self.member)
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── List ──────────────────────────────────────────────

    def test_list_is_filtered_by_the_viewer_file_permission(self):
        private = self._make_file(self.admin, "private.txt")
        shared = self._make_file(self.admin, "shared.txt")
        share_file(
            shared, target_user=self.member, permission="r", acting_user=self.admin
        )
        link_files(self.admin, self.task, [private, shared])

        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(
            [f["name"] for f in resp.data["files"]], ["private.txt", "shared.txt"]
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual([f["name"] for f in resp.data["files"]], ["shared.txt"])

    def test_list_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Destroy ───────────────────────────────────────────

    def test_member_unlinks_a_file(self):
        src = self._make_file(self.admin)
        (link,) = link_files(self.admin, self.task, [src])
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{link.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskFileLink.objects.exists())
        self.assertTrue(
            self.task.events.filter(
                type=TaskEvent.Type.FILE_UNLINKED, actor=self.member
            ).exists()
        )

    def test_unlinking_a_file_the_viewer_cannot_see_is_still_allowed(self):
        # The link is task data: any project member may drop it, whether or
        # not they can open the file behind it.
        src = self._make_file(self.admin)
        (link,) = link_files(self.admin, self.task, [src])
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{link.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_unlink_unknown_link_is_404(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{uuid.uuid4()}")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_link_from_another_task_is_404(self):
        other_task = create_task(self.project, self.admin, title="Other")
        src = self._make_file(self.admin)
        (link,) = link_files(self.admin, other_task, [src])
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{link.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(TaskFileLink.objects.filter(pk=link.pk).exists())
