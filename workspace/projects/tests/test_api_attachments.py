"""Task attachments API: uploading, copying from workspace, listing, download.

The blob belongs to the task: anyone with task access sees every
attachment. Attaching a workspace file copies its content, so the source
file's lifecycle never reaches the task copy.
"""

import uuid

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models.signals import post_save
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.services import tree
from workspace.projects.models import TaskAttachment, TaskEvent
from workspace.projects.services.attachments import create_attachments
from workspace.projects.services.tasks import create_task, delete_task
from workspace.projects.tests.base import ProjectTestMixin


class TaskAttachmentApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.url = f"/api/v1/projects/tasks/{self.task.uuid}/attachments"

    def _item_url(self, uuid_, suffix=""):
        return f"/api/v1/projects/attachments/{uuid_}{suffix}"

    def tearDown(self):
        for att in TaskAttachment.objects.all():
            att.file.delete(save=False)

    def _make_file(self, owner, name="doc.txt", content=b"hello"):
        return tree.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, content, content_type="text/plain"),
        )

    def _attach_workspace(self, user, *files):
        self.client.force_authenticate(user)
        return self.client.post(
            self.url,
            data={"files": [str(f.uuid) for f in files]},
            format="json",
        )

    def _upload(self, user, name="notes.txt", content=b"minutes"):
        self.client.force_authenticate(user)
        return self.client.post(
            self.url,
            data={"file": [SimpleUploadedFile(name, content)]},
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
        (created,) = resp.data
        self.assertEqual(created["name"], "doc.txt")
        self.assertEqual(created["uuid"], str(att.uuid))

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
        src.delete()
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
            self.url, data={"files": [str(uuid.uuid4())]}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_vanished_source_blob_is_rejected(self):
        src = self._make_file(self.member)
        default_storage.delete(src.content.name)
        resp = self._attach_workspace(self.member, src)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_a_refused_request_leaves_no_blob_behind(self):
        """An upload stored before the copy of a workspace file fails loses
        its row to the rollback: its bytes must go too."""
        src = self._make_file(self.member)
        default_storage.delete(src.content.name)
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url,
            data={
                "file": [SimpleUploadedFile("notes.txt", b"minutes")],
                "files": [str(src.uuid)],
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskAttachment.objects.count(), 0)
        self.assertEqual(
            list(default_storage.iter_blobs(f"projects/tasks/{self.task.uuid}")), []
        )

    def test_a_blob_whose_row_never_landed_is_dropped_too(self):
        """The bytes are stored before the row is written: a failure between
        the two leaves a blob no row ever pointed at."""

        def fail_once_stored(sender, **kwargs):
            raise RuntimeError("after the blob, before the save returns")

        post_save.connect(fail_once_stored, sender=TaskAttachment)
        self.addCleanup(post_save.disconnect, fail_once_stored, sender=TaskAttachment)

        with self.assertRaises(RuntimeError):
            create_attachments(
                self.member,
                self.task,
                [SimpleUploadedFile("notes.txt", b"minutes")],
                [],
            )

        self.assertEqual(TaskAttachment.objects.count(), 0)
        self.assertEqual(
            list(default_storage.iter_blobs(f"projects/tasks/{self.task.uuid}")), []
        )

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
        with patch("workspace.projects.serializers.MAX_UPLOAD_BYTES", 4):
            resp = self.client.post(
                self.url,
                data={"file": [SimpleUploadedFile("big.bin", b"12345")]},
                format="multipart",
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["file"])
        self.assertEqual(TaskAttachment.objects.count(), 0)

    # ── Listing ────────────────────────────────────────────────

    def test_every_member_sees_an_uploaded_attachment(self):
        # The old link model hid uploads from everyone but the uploader;
        # the blob now belongs to the task, so task access is enough.
        self._upload(self.member)
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        (listed,) = resp.data
        self.assertEqual(listed["name"], "notes.txt")
        self.assertEqual(listed["added_by"]["username"], "member1")
        self.assertEqual(
            (listed["content_url"], listed["download_url"]),
            (
                self._item_url(listed["uuid"], "/content"),
                self._item_url(listed["uuid"], "/download"),
            ),
        )

    def test_member_sees_a_copy_of_a_file_they_cannot_access(self):
        private = self._make_file(self.admin, "private.txt")
        self._attach_workspace(self.admin, private)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual([item["name"] for item in resp.data], ["private.txt"])

    def test_attachment_survives_source_file_trash(self):
        from django.utils import timezone

        src = self._make_file(self.member)
        self._attach_workspace(self.member, src)
        src.deleted_at = timezone.now()
        src.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data), 1)

    def test_outsider_cannot_list(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Downloading ────────────────────────────────────────────

    def test_member_downloads_an_attachment(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self._item_url(att.uuid, "/download"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(resp.streaming_content), b"minutes")
        self.assertTrue(resp["Content-Disposition"].startswith("attachment"))

    def test_content_is_served_inline(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self._item_url(att.uuid, "/content"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(resp.streaming_content), b"minutes")
        self.assertTrue(resp["Content-Disposition"].startswith("inline"))

    def test_member_reads_one_attachment(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self._item_url(att.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "notes.txt")

    def test_an_html_attachment_is_never_shown_inline(self):
        # Opened directly, the page would run with the viewer's session.
        self._upload(
            self.member,
            name="page.html",
            content=b"<html><script>alert(1)</script></html>",
        )
        att = TaskAttachment.objects.get()
        self.assertEqual(att.mime_type, "text/html")
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self._item_url(att.uuid, "/content"))
        b"".join(resp.streaming_content)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", resp["Content-Security-Policy"])

    def test_outsider_cannot_download(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        self.client.force_authenticate(self.outsider)
        for suffix in ("", "/content", "/download"):
            with self.subTest(suffix=suffix):
                hidden = self.client.get(self._item_url(att.uuid, suffix))
                unknown = self.client.get(self._item_url(uuid.uuid4(), suffix))
                self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (unknown.status_code, unknown.content),
                )

    def test_download_of_vanished_blob_404s(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        default_storage.delete(att.file.name)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self._item_url(att.uuid, "/download"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Removing ───────────────────────────────────────────────

    def test_remove_deletes_the_row_and_the_blob(self):
        self._upload(self.member)
        att = TaskAttachment.objects.get()
        blob_path = att.file.name
        self.client.force_authenticate(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.delete(self._item_url(att.uuid))
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
        self.client.delete(self._item_url(att.uuid))
        src.refresh_from_db()
        self.assertIsNone(src.deleted_at)
        self.assertTrue(default_storage.exists(src.content.name))

    def test_remove_unknown_uuid_404s(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self._item_url(uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Cascades ───────────────────────────────────────────────

    def test_deleting_the_task_removes_its_attachments(self):
        self._upload(self.member)
        self.task.delete()
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_deleting_the_task_deletes_the_blobs_once_committed(self):
        self._upload(self.member)
        blob_path = TaskAttachment.objects.get().file.name
        self.client.force_authenticate(self.admin)

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.delete(f"/api/v1/projects/tasks/{self.task.uuid}")

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(default_storage.exists(blob_path))

    def test_deleting_the_project_deletes_the_blobs_once_committed(self):
        self._upload(self.member)
        blob_path = TaskAttachment.objects.get().file.name
        self.client.force_authenticate(self.admin)

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.delete(f"/api/v1/projects/{self.project.uuid}")

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(default_storage.exists(blob_path))

    def test_a_rolled_back_delete_keeps_the_blob(self):
        self._upload(self.member)
        blob_path = TaskAttachment.objects.get().file.name

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    delete_task(self.task)
                    raise RuntimeError("abort")
            except RuntimeError:
                pass

        self.assertTrue(default_storage.exists(blob_path))


class TaskAttachmentViewerTests(ProjectTestMixin, APITestCase):
    """The /projects/view-attachment/<uuid> panel behind the viewer modal."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.client.force_authenticate(self.member)
        self.client.post(
            f"/api/v1/projects/tasks/{self.task.uuid}/attachments",
            data={"file": [SimpleUploadedFile("notes.txt", b"minutes")]},
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
