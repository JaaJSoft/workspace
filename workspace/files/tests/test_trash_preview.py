"""What the trash view can still show of a trashed file.

Its owner sees its thumbnail and opens it in the viewer, read-only. Anyone
else lost access to it with the trash, a share included.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.test import APITestCase

from workspace.files.actions import ActionRegistry
from workspace.files.models import FileShare
from workspace.files.services import FilePermission, FileService
from workspace.files.services.thumbnails.generation import get_thumbnail_path

User = get_user_model()


class TrashedFilePreviewTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.file = FileService.create_file(
            self.owner,
            "notes.txt",
            content=ContentFile(b"trashed bytes", name="notes.txt"),
        )
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.other,
            permission=FileShare.Permission.READ_ONLY,
        )
        default_storage.save(
            get_thumbnail_path(self.file.uuid), ContentFile(b"fake-webp-bytes")
        )
        FileService.soft_delete(self.file, acting_user=self.owner)
        self.file.refresh_from_db()

    def _body(self, resp):
        return b"".join(resp.streaming_content)

    def test_the_owner_gets_the_thumbnail(self):
        self.client.force_authenticate(user=self.owner)

        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/thumbnail")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._body(resp), b"fake-webp-bytes")

    def test_the_owner_gets_the_content_from_the_trash(self):
        self.assertTrue(self.file.content.name.startswith("trash/"))
        self.client.force_authenticate(user=self.owner)

        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/content")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._body(resp), b"trashed bytes")

    def test_a_share_no_longer_reaches_a_trashed_file(self):
        self.client.force_authenticate(user=self.other)

        for endpoint in ("thumbnail", "content"):
            with self.subTest(endpoint=endpoint):
                resp = self.client.get(f"/api/v1/files/{self.file.uuid}/{endpoint}")
                self.assertEqual(resp.status_code, 404)

    def test_the_owner_opens_it_in_the_viewer_read_only(self):
        self.client.force_login(self.owner)

        resp = self.client.get(f"/files/view/{self.file.uuid}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "canEdit: false")

    def test_the_viewer_is_out_of_reach_of_a_share(self):
        self.client.force_login(self.other)

        resp = self.client.get(f"/files/view/{self.file.uuid}")

        self.assertEqual(resp.status_code, 404)

    def test_the_trash_offers_to_open_it(self):
        action = ActionRegistry.get("view")

        self.assertTrue(
            action.is_available(self.owner, self.file, permission=FilePermission.MANAGE)
        )


class LiveFileViewerTests(APITestCase):
    def test_a_live_file_stays_editable_in_the_viewer(self):
        owner = User.objects.create_user(username="carol", password="pass")
        live = FileService.create_file(
            owner, "live.txt", content=ContentFile(b"live", name="live.txt")
        )
        self.client.force_login(owner)

        resp = self.client.get(f"/files/view/{live.uuid}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "canEdit: true")
