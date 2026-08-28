"""Task attachments API: uploading, copying from workspace, listing, download.

The blob belongs to the task: anyone with task access sees every
attachment. Attaching a workspace file copies its content, so the source
file's lifecycle never reaches the task copy.
"""

import uuid

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.services import FileService
from workspace.projects.models import TaskAttachment, TaskEvent
from workspace.projects.services.tasks import create_task
from workspace.projects.tests.base import ProjectTestMixin


class TaskAttachmentApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.url = (
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/attachments"
        )

    def tearDown(self):
        for att in TaskAttachment.objects.all():
            att.file.delete(save=False)

    def _make_file(self, owner, name="doc.txt", content=b"hello"):
        return FileService.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, content, content_type="text/plain"),
        )

    def _attach_workspace(self, user, *files):
        self.client.force_authenticate(user)
        return self.client.post(
            self.url,
            data={"file_uuids": [str(f.uuid) for f in files]},
            format="json",
        )

    def _upload(self, user, name="notes.txt", content=b"minutes"):
        self.client.force_authenticate(user)
        return self.client.post(
            self.url,
            data={"files": [SimpleUploadedFile(name, content)]},
            format="multipart",
        )

    # ── Attaching from the workspace ───────────────────────────

    def test_member_attaches_an_accessible_workspace_file(self):
        src = self._make_file(self.member)
        resp = self._attach_workspace(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        att = TaskAttachment.objects.get()
        self.assertEqual(att.original_name, "doc.txt")
        self.assertEqual(att.added_by, self.member)
        self.assertEqual(resp.data["attachments"][0]["name"], "doc.txt")

    def test_workspace_attach_copies_the_content(self):
        src = self._make_file(self.member, content=b"payload")
        self._attach_workspace(self.member, src)
        att = TaskAttachment.objects.get()
        # A shared blob would also pass a content check - the storage paths
        # must differ so deleting the source cannot orphan the copy.
        self.assertNotEqual(att.file.name, src.content.name)
        with att.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"payload")

    def test_attachment_survives_source_file_hard_delete(self):
        src = self._make_file(self.member, content=b"payload")
        self._attach_workspace(self.member, src)
        src.delete(hard=True)
        att = TaskAttachment.objects.get()
        with att.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"payload")

    def test_attaching_records_an_activity_event(self):
        src = self._make_file(self.member)
        self._attach_workspace(self.member, src)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.ATTACHED, actor=self.member
            ).exists()
        )

    def test_attaching_a_file_the_member_cannot_access_is_rejected(self):
        foreign = self._make_file(self.admin)
        resp = self._attach_workspace(self.member, foreign)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_unknown_uuid_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url, data={"file_uuids": [str(uuid.uuid4())]}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_vanished_source_blob_is_rejected(self):
        src = self._make_file(self.member)
        default_storage.delete(src.content.name)
        resp = self._attach_workspace(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_empty_request_is_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_outsider_gets_404(self):
        src = self._make_file(self.outsider)
        resp = self._attach_workspace(self.outsider, src)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_archived_project_rejects_attaching(self):
        from django.utils import timezone

        src = self._make_file(self.member)
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        resp = self._attach_workspace(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Uploading ──────────────────────────────────────────────

    def test_upload_stores_the_blob_on_the_task(self):
        resp = self._upload(self.member)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        att = TaskAttachment.objects.get()
        self.assertEqual(att.original_name, "notes.txt")
        self.assertEqual(att.size, len(b"minutes"))
        self.assertIn(f"projects/tasks/{self.task.uuid}/", att.file.name)
        with att.file.open("rb") as fh:
            self.assertEqual(fh.read(), b"minutes")

    def test_upload_does_not_create_a_workspace_file(self):
        from workspace.files.models import File

        self._upload(self.member)
        self.assertFalse(File.objects.filter(owner=self.member).exists())

    def test_oversized_upload_is_rejected(self):
        from unittest.mock import patch

        self.client.force_authenticate(self.member)
        with patch("workspace.projects.views.viewsets.MAX_UPLOAD_BYTES", 4):
            resp = self.client.post(
                self.url,
                data={"files": [SimpleUploadedFile("big.bin", b"12345")]},
                format="multipart",
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    # ── Listing ────────────────────────────────────────────────

    def test_every_member_sees_an_uploaded_attachment(self):
        # The old link model hid uploads from everyone but the uploader;
        # the blob now belongs to the task, so task access is enough.
        self._upload(self.member)
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["name"], "notes.txt")
        self.assertEqual(resp.data["attachments"][0]["added_by"], "member1")

    def test_member_sees_a_copy_of_a_file_they_cannot_access(self):
        private = self._make_file(self.admin, "private.txt")
        self._attach_workspace(self.admin, private)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["name"], "private.txt")

    def test_attachment_survives_source_file_trash(self):
        from django.utils import timezone

        src = self._make_file(self.member)
        self._attach_workspace(self.member, src)
        src.deleted_at = timezone.now()
        src.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data["attachments"]), 1)

    def test_outsider_cannot_list(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Downloading ────────────────────────────────────────────

    def test_member_downloads_an_attachment(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.admin)
        resp = self.client.get(f"{self.url}/{att.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(resp.streaming_content), b"minutes")

    def test_outsider_cannot_download(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(f"{self.url}/{att.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_of_vanished_blob_404s(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        default_storage.delete(att.file.name)
        self.client.force_authenticate(self.member)
        resp = self.client.get(f"{self.url}/{att.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Removing ───────────────────────────────────────────────

    def test_remove_deletes_the_row_and_the_blob(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        blob_path = att.file.name
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{att.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(TaskAttachment.objects.count(), 0)
        self.assertFalse(default_storage.exists(blob_path))
        self.assertTrue(
            TaskEvent.objects.filter(
                task=self.task, type=TaskEvent.Type.DETACHED
            ).exists()
        )

    def test_remove_keeps_the_source_workspace_file(self):
        src = self._make_file(self.member)
        self._attach_workspace(self.member, src)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.member)
        self.client.delete(f"{self.url}/{att.uuid}")
        src.refresh_from_db()
        self.assertIsNone(src.deleted_at)
        self.assertTrue(default_storage.exists(src.content.name))

    def test_remove_unknown_uuid_404s(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(f"{self.url}/{uuid.uuid4()}")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Cascades ───────────────────────────────────────────────

    def test_deleting_the_task_removes_its_attachments(self):
        self._upload(self.member)
        self.task.delete()
        self.assertEqual(TaskAttachment.objects.count(), 0)


class TaskAttachmentViewerTests(ProjectTestMixin, APITestCase):
    """The /projects/view-attachment/<uuid> panel behind the viewer modal."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.client.force_authenticate(self.member)
        self.client.post(
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/attachments",
            data={"files": [SimpleUploadedFile("notes.txt", b"minutes")]},
            format="multipart",
        )
        self.attachment = TaskAttachment.objects.get()

    def tearDown(self):
        for att in TaskAttachment.objects.all():
            att.file.delete(save=False)

    def test_member_gets_a_viewer_panel(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/view-attachment/{self.attachment.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"viewer-panel", resp.content)

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(f"/projects/view-attachment/{self.attachment.uuid}")
        self.assertEqual(resp.status_code, 404)
