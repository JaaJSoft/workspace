from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShareLink
from workspace.files.services.sharing import ShareLinkRuleError, create_share_link

User = get_user_model()


class ShareLinkModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.folder = File.objects.create(
            owner=self.user, name="Contracts", node_type=File.NodeType.FOLDER
        )
        self.doc = File.objects.create(
            owner=self.user, name="doc.txt", node_type=File.NodeType.FILE
        )

    def test_read_is_the_default_mode(self):
        link = create_share_link(self.doc, acting_user=self.user)
        self.assertEqual(link.mode, FileShareLink.Mode.READ)
        self.assertTrue(link.allows_read)
        self.assertFalse(link.allows_upload)

    def test_drop_mode_reads_nothing_and_writes(self):
        link = create_share_link(
            self.folder, acting_user=self.user, mode=FileShareLink.Mode.DROP
        )
        self.assertFalse(link.allows_read)
        self.assertTrue(link.allows_upload)

    def test_both_mode_reads_and_writes(self):
        link = create_share_link(
            self.folder, acting_user=self.user, mode=FileShareLink.Mode.BOTH
        )
        self.assertTrue(link.allows_read)
        self.assertTrue(link.allows_upload)

    def test_a_file_target_refuses_an_upload_mode(self):
        with self.assertRaises(ShareLinkRuleError):
            create_share_link(
                self.doc, acting_user=self.user, mode=FileShareLink.Mode.DROP
            )

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ShareLinkRuleError):
            create_share_link(self.folder, acting_user=self.user, mode="admin")

    def test_caps_default_to_null_and_counters_to_zero(self):
        link = create_share_link(
            self.folder, acting_user=self.user, mode=FileShareLink.Mode.DROP
        )
        self.assertIsNone(link.max_file_bytes)
        self.assertIsNone(link.max_file_count)
        self.assertEqual(link.upload_count, 0)
        self.assertEqual(link.notified_upload_count, 0)


class ShareLinkCreationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.client.force_authenticate(user=self.user)
        self.folder = File.objects.create(
            owner=self.user, name="Contracts", node_type=File.NodeType.FOLDER
        )
        self.doc = File.objects.create(
            owner=self.user, name="doc.txt", node_type=File.NodeType.FILE
        )

    def test_creating_a_folder_drop_link(self):
        resp = self.client.post(
            f"/api/v1/files/{self.folder.uuid}/share-links",
            {"mode": "drop", "max_file_bytes": 1024, "max_file_count": 5},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["mode"], "drop")
        self.assertEqual(resp.data["node_type"], "folder")
        self.assertEqual(resp.data["max_file_bytes"], 1024)
        self.assertEqual(resp.data["max_file_count"], 5)
        self.assertEqual(resp.data["upload_count"], 0)

    def test_creating_a_file_drop_link_is_a_400(self):
        resp = self.client.post(
            f"/api/v1/files/{self.doc.uuid}/share-links",
            {"mode": "drop"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_creating_a_folder_read_link_now_works(self):
        resp = self.client.post(
            f"/api/v1/files/{self.folder.uuid}/share-links", {}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["mode"], "read")

    def test_a_cap_beyond_the_column_is_a_400(self):
        """PostgreSQL raises a DataError above the column ceiling and SQLite
        silently stores it, so the refusal has to happen before the write."""
        for field, value in (
            ("max_file_bytes", 9223372036854775808),
            ("max_file_count", 2147483648),
        ):
            with self.subTest(field=field):
                resp = self.client.post(
                    f"/api/v1/files/{self.folder.uuid}/share-links",
                    {"mode": "drop", field: value},
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_cap_at_the_column_ceiling_is_accepted(self):
        resp = self.client.post(
            f"/api/v1/files/{self.folder.uuid}/share-links",
            {
                "mode": "drop",
                "max_file_bytes": 9223372036854775807,
                "max_file_count": 2147483647,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_a_negative_cap_is_a_400(self):
        resp = self.client.post(
            f"/api/v1/files/{self.folder.uuid}/share-links",
            {"mode": "drop", "max_file_count": -1},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
