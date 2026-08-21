"""Task attachments API: linking, uploading, listing and unlinking files.

The attachment is a pure link row - the file lives in the files module and
keeps its own permissions. Seeing an attachment requires both task access
(project membership) and file permission; the link never widens file access.
"""

import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare
from workspace.files.services import FileService
from workspace.projects.models import TaskAttachment, TaskEvent
from workspace.projects.services.attachments import UPLOADS_FOLDER_NAME
from workspace.projects.services.tasks import create_task
from workspace.projects.tests.base import ProjectTestMixin


class TaskAttachmentApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.url = (
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/attachments"
        )

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

    # ── Linking ────────────────────────────────────────────────

    def test_member_links_an_accessible_file(self):
        src = self._make_file(self.member)
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        link = TaskAttachment.objects.get()
        self.assertEqual(link.file, src)
        self.assertEqual(link.added_by, self.member)
        self.assertEqual(resp.data["attachments"][0]["file"]["name"], "doc.txt")

    def test_linking_records_an_activity_event(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.ATTACHED, actor=self.member
            ).exists()
        )

    def test_relinking_the_same_file_is_idempotent(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TaskAttachment.objects.count(), 1)
        self.assertEqual(
            TaskEvent.objects.filter(type=TaskEvent.Type.ATTACHED).count(), 1
        )

    def test_linking_a_file_the_member_cannot_access_is_rejected(self):
        foreign = self._make_file(self.admin)
        resp = self._link(self.member, foreign)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_unknown_uuid_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url, data={"file_uuids": [str(uuid.uuid4())]}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_request_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_outsider_gets_404(self):
        src = self._make_file(self.outsider)
        resp = self._link(self.outsider, src)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_archived_project_rejects_linking(self):
        from django.utils import timezone

        src = self._make_file(self.member)
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        resp = self._link(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Uploading ──────────────────────────────────────────────

    def test_upload_creates_a_file_in_the_uploaders_folder_and_links_it(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url,
            data={"files": [SimpleUploadedFile("notes.txt", b"minutes")]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        link = TaskAttachment.objects.get()
        self.assertEqual(link.file.owner, self.member)
        self.assertEqual(link.file.parent.name, UPLOADS_FOLDER_NAME)
        with link.file.content.open("rb") as f:
            self.assertEqual(f.read(), b"minutes")

    def test_uploads_reuse_the_existing_folder(self):
        self.client.force_authenticate(self.member)
        for name in ("a.txt", "b.txt"):
            self.client.post(
                self.url,
                data={"files": [SimpleUploadedFile(name, b"x")]},
                format="multipart",
            )
        self.assertEqual(
            File.objects.filter(
                owner=self.member, name=UPLOADS_FOLDER_NAME, node_type="folder"
            ).count(),
            1,
        )

    def test_oversized_upload_is_rejected(self):
        from unittest.mock import patch

        self.client.force_authenticate(self.member)
        with patch("workspace.projects.viewsets.MAX_UPLOAD_BYTES", 4):
            resp = self.client.post(
                self.url,
                data={"files": [SimpleUploadedFile("big.bin", b"12345")]},
                format="multipart",
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    # ── Listing ────────────────────────────────────────────────

    def test_list_hides_files_the_viewer_cannot_access(self):
        # admin links their own private file; member has task access but no
        # file permission - the link must not widen file access.
        private = self._make_file(self.admin, "private.txt")
        self._link(self.admin, private)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["attachments"], [])

    def test_list_shows_files_shared_with_the_viewer(self):
        shared = self._make_file(self.admin, "spec.txt")
        FileShare.objects.create(
            file=shared,
            shared_by=self.admin,
            shared_with=self.member,
            permission=FileShare.Permission.READ_ONLY,
        )
        self._link(self.admin, shared)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["file"]["name"], "spec.txt")
        self.assertEqual(resp.data["attachments"][0]["added_by"], "admin1")

    def test_list_hides_trashed_files(self):
        from django.utils import timezone

        src = self._make_file(self.member)
        self._link(self.member, src)
        src.deleted_at = timezone.now()
        src.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.data["attachments"], [])

    def test_outsider_cannot_list(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Unlinking ──────────────────────────────────────────────

    def test_unlink_removes_the_link_but_keeps_the_file(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        link = TaskAttachment.objects.get()
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{link.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(TaskAttachment.objects.count(), 0)
        src.refresh_from_db()
        self.assertIsNone(src.deleted_at)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.DETACHED
            ).exists()
        )

    def test_unlink_unknown_uuid_404s(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{uuid.uuid4()}")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Cascades ───────────────────────────────────────────────

    def test_deleting_the_task_removes_its_links_only(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        self.task.delete()
        self.assertEqual(TaskAttachment.objects.count(), 0)
        src.refresh_from_db()
        self.assertIsNone(src.deleted_at)

    def test_hard_deleting_the_file_removes_the_link(self):
        src = self._make_file(self.member)
        self._link(self.member, src)
        src.delete(hard=True)
        self.assertEqual(TaskAttachment.objects.count(), 0)
