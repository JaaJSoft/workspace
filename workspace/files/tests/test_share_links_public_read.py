from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShareLink
from workspace.files.services.thumbnails.generation import get_thumbnail_path

User = get_user_model()


class SharedFolderReadTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.root = File.objects.create(
            owner=self.owner, name="Shared", node_type=File.NodeType.FOLDER
        )
        self.sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.doc = File.objects.create(
            owner=self.owner,
            name="in.txt",
            node_type=File.NodeType.FILE,
            parent=self.sub,
            mime_type="text/plain",
            category="text",
            size=5,
            content=ContentFile(b"hello", name="in.txt"),
        )
        self.outside = File.objects.create(
            owner=self.owner, name="out.txt", node_type=File.NodeType.FILE
        )
        self.read_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        self.drop_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )

    def test_meta_describes_a_folder_link(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.read_link.token}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["kind"], "folder")
        self.assertEqual(resp.data["mode"], "read")
        self.assertTrue(resp.data["allows_read"])
        self.assertFalse(resp.data["allows_upload"])
        self.assertEqual(resp.data["name"], "Shared")

    def test_meta_of_a_drop_link_says_nothing_about_contents(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.drop_link.token}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Shared")
        self.assertTrue(resp.data["allows_upload"])
        for leaked in ("size", "child_count", "mime_type", "category", "is_viewable"):
            self.assertNotIn(leaked, resp.data)

    def test_entries_lists_the_root(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.read_link.token}/entries")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [e["name"] for e in resp.data["entries"]]
        self.assertEqual(names, ["Sub"])
        self.assertEqual(
            resp.data["breadcrumbs"], [{"uuid": str(self.root.uuid), "name": "Shared"}]
        )

    def test_entries_descends(self):
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/entries",
            {"folder": str(self.sub.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([e["name"] for e in resp.data["entries"]], ["in.txt"])
        self.assertEqual(
            [b["name"] for b in resp.data["breadcrumbs"]], ["Shared", "Sub"]
        )

    def test_entries_never_exposes_the_absolute_path(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.read_link.token}/entries")
        self.assertNotIn("path", resp.data["entries"][0])

    def test_entries_refuses_a_folder_outside_the_subtree(self):
        stranger = File.objects.create(
            owner=self.owner, name="Elsewhere", node_type=File.NodeType.FOLDER
        )
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/entries",
            {"folder": str(stranger.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_entries_is_refused_on_a_drop_link(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.drop_link.token}/entries")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_entries_paginates_when_asked(self):
        for index in range(5):
            File.objects.create(
                owner=self.owner,
                name=f"f{index}.txt",
                node_type=File.NodeType.FILE,
                parent=self.root,
            )
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/entries", {"limit": 2}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["entries"]), 2)
        self.assertEqual(resp["X-Has-More"], "true")

    def test_entries_defaults_to_a_bounded_page(self):
        """No ``limit`` given must not dump the whole folder in one request."""
        for index in range(201):
            File.objects.create(
                owner=self.owner,
                name=f"f{index:03}.txt",
                node_type=File.NodeType.FILE,
                parent=self.root,
            )
        resp = self.client.get(f"/api/v1/files/shared/{self.read_link.token}/entries")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["entries"]), 200)
        self.assertEqual(resp["X-Has-More"], "true")

    def test_download_reaches_a_descendant(self):
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/download",
            {"file": str(self.doc.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(resp.streaming_content), b"hello")

    def test_download_refuses_a_file_outside_the_subtree(self):
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/download",
            {"file": str(self.outside.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_is_refused_on_a_drop_link(self):
        resp = self.client.get(
            f"/api/v1/files/shared/{self.drop_link.token}/download",
            {"file": str(self.doc.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_reading_a_folder_root_directly_is_refused(self):
        """A folder has no content: the content endpoint must not 500 on it."""
        resp = self.client.get(f"/api/v1/files/shared/{self.read_link.token}/content")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_outside_the_subtree_does_not_record_a_view(self):
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 0)
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/download",
            {"file": str(self.outside.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 0)

    def test_download_of_a_real_descendant_records_a_view(self):
        self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/download",
            {"file": str(self.doc.uuid)},
        )
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 1)

    def test_thumbnail_is_refused_on_a_drop_link(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.drop_link.token}/thumbnail")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_thumbnail_refuses_a_file_outside_the_subtree(self):
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/thumbnail",
            {"file": str(self.outside.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_thumbnail_serves_the_blob_and_records_a_view(self):
        self.doc.has_thumbnail = True
        self.doc.save(update_fields=["has_thumbnail"])
        default_storage.save(
            get_thumbnail_path(self.doc.uuid), ContentFile(b"webp-bytes")
        )
        resp = self.client.get(
            f"/api/v1/files/shared/{self.read_link.token}/thumbnail",
            {"file": str(self.doc.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "image/webp")
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 1)
