from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileShareLink
from workspace.files.services.quota import QuotaExceeded

User = get_user_model()


def part(name="report.pdf", body=b"hello"):
    return SimpleUploadedFile(name, body, content_type="application/octet-stream")


class ShareLinkUploadTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.root = File.objects.create(
            owner=self.owner, name="Drop", node_type=File.NodeType.FOLDER
        )
        self.link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        self.url = f"/api/v1/files/shared/{self.link.token}/upload"

    def tearDown(self):
        cache.clear()

    def post(self, url=None, **kwargs):
        return self.client.post(
            url or self.url, {"file": part(**kwargs)}, format="multipart"
        )

    def test_a_successful_upload_returns_an_empty_204(self):
        resp = self.post()
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(resp.content)
        self.assertTrue(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )

    def test_the_file_belongs_to_the_owner_not_to_nobody(self):
        self.post()
        uploaded = File.objects.get(parent=self.root, name="report.pdf")
        self.assertEqual(uploaded.owner, self.owner)

    def test_the_audit_row_names_no_actor(self):
        self.post()
        uploaded = File.objects.get(parent=self.root, name="report.pdf")
        event = FileEvent.objects.get(
            file=uploaded, action=FileEvent.Action.LINK_UPLOAD
        )
        self.assertIsNone(event.actor)
        self.assertEqual(event.metadata["link_uuid"], str(self.link.uuid))
        created_event = FileEvent.objects.get(
            file=uploaded, action=FileEvent.Action.CREATED
        )
        self.assertIsNone(created_event.actor)

    def test_the_counter_advances(self):
        self.post()
        self.link.refresh_from_db()
        self.assertEqual(self.link.upload_count, 1)

    def test_an_upload_is_not_counted_as_a_view(self):
        """view_count means reads, so an owner can tell reads from writes."""
        self.post()
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 0)

    def test_a_read_link_refuses_uploads(self):
        read_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        resp = self.post(url=f"/api/v1/files/shared/{read_link.token}/upload")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_both_mode_link_accepts_uploads(self):
        both_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.BOTH
        )
        resp = self.post(url=f"/api/v1/files/shared/{both_link.token}/upload")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_missing_expired_and_wrong_mode_are_indistinguishable(self):
        expired = FileShareLink.objects.create(
            file=self.root,
            created_by=self.owner,
            mode=FileShareLink.Mode.DROP,
            expires_at=timezone.now() - timedelta(days=1),
        )
        read_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        answers = [
            self.post(url="/api/v1/files/shared/does-not-exist/upload"),
            self.post(url=f"/api/v1/files/shared/{expired.token}/upload"),
            self.post(url=f"/api/v1/files/shared/{read_link.token}/upload"),
        ]
        self.assertEqual({r.status_code for r in answers}, {status.HTTP_404_NOT_FOUND})
        self.assertEqual(len({r.content for r in answers}), 1)

    def test_a_name_clash_renames_and_never_replaces(self):
        existing = File.objects.create(
            owner=self.owner,
            name="report.pdf",
            node_type=File.NodeType.FILE,
            parent=self.root,
            content=ContentFile(b"original", name="report.pdf"),
            size=8,
        )
        resp = self.post()
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        existing.refresh_from_db()
        with existing.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"original")
        self.assertEqual(File.objects.filter(parent=self.root).count(), 2)

    def test_a_clash_and_a_clean_name_answer_identically(self):
        """The response must not be an oracle for what is already in the folder."""
        clean = self.post(name="unique-a.pdf")
        File.objects.create(
            owner=self.owner,
            name="taken.pdf",
            node_type=File.NodeType.FILE,
            parent=self.root,
        )
        clashing = self.post(name="taken.pdf")
        self.assertEqual(clean.status_code, clashing.status_code)
        self.assertEqual(clean.content, clashing.content)

    def test_a_traversing_name_is_sanitised(self):
        self.post(name="../../etc/passwd")
        self.assertTrue(File.objects.filter(parent=self.root, name="passwd").exists())

    def test_a_maximum_length_name_uploaded_twice_still_fits_the_column(self):
        long_name = "a" * 251 + ".pdf"  # 255 characters, File.name's max_length
        first = self.post(name=long_name)
        second = self.post(name=long_name)
        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_204_NO_CONTENT)
        names = list(
            File.objects.filter(parent=self.root).values_list("name", flat=True)
        )
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertLessEqual(len(name), 255)

    def test_a_body_without_a_file_part_is_a_400(self):
        resp = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_more_than_one_part_is_a_400(self):
        resp = self.client.post(
            self.url, {"file": [part("a.txt"), part("b.txt")]}, format="multipart"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(FILES_DROP_MAX_FILE_BYTES=3)
    def test_the_global_byte_ceiling_refuses_with_413(self):
        resp = self.post(body=b"too long")
        self.assertEqual(resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertFalse(File.objects.filter(parent=self.root).exists())

    def test_the_per_link_byte_cap_refuses_with_413(self):
        self.link.max_file_bytes = 2
        self.link.save(update_fields=["max_file_bytes"])
        resp = self.post(body=b"too long")
        self.assertEqual(resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_the_count_cap_refuses_with_403(self):
        self.link.max_file_count = 1
        self.link.save(update_fields=["max_file_count"])
        self.assertEqual(self.post(name="a.txt").status_code, 204)
        self.assertEqual(self.post(name="b.txt").status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(FILES_DROP_MAX_FILE_COUNT=1)
    def test_an_uncapped_link_is_still_bounded_by_the_global_ceiling(self):
        self.assertEqual(self.post(name="a.txt").status_code, 204)
        self.assertEqual(self.post(name="b.txt").status_code, status.HTTP_403_FORBIDDEN)

    def test_a_failed_create_releases_the_reserved_slot(self):
        with patch(
            "workspace.files.services.files.FileService.create_file",
            side_effect=QuotaExceeded(),
        ):
            resp = self.post()
        self.assertEqual(resp.status_code, 413)
        self.link.refresh_from_db()
        self.assertEqual(self.link.upload_count, 0)

    def test_a_password_is_enforced_on_the_write_path(self):
        from django.contrib.auth.hashers import make_password

        self.link.password = make_password("secret")
        self.link.save(update_fields=["password"])
        resp = self.post()
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_valid_access_token_unlocks_the_write_path(self):
        from django.contrib.auth.hashers import make_password

        from workspace.files.views.share_links import SIGNER

        self.link.password = make_password("secret")
        self.link.save(update_fields=["password"])
        resp = self.client.post(
            self.url,
            {"file": part()},
            format="multipart",
            HTTP_X_SHARE_ACCESS=SIGNER.sign(self.link.token),
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_a_query_parameter_access_token_does_not_unlock_the_write_path(self):
        from django.contrib.auth.hashers import make_password

        from workspace.files.views.share_links import SIGNER

        self.link.password = make_password("secret")
        self.link.save(update_fields=["password"])
        resp = self.client.post(
            f"{self.url}?access_token={SIGNER.sign(self.link.token)}",
            {"file": part()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_cross_link_access_token_does_not_unlock_the_write_path(self):
        from django.contrib.auth.hashers import make_password

        from workspace.files.views.share_links import SIGNER

        self.link.password = make_password("secret")
        self.link.save(update_fields=["password"])
        other_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        resp = self.client.post(
            self.url,
            {"file": part()},
            format="multipart",
            HTTP_X_SHARE_ACCESS=SIGNER.sign(other_link.token),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_full_quota_refuses_with_413_and_creates_nothing(self):
        with patch(
            "workspace.files.services.files.check_write_allowed",
            side_effect=QuotaExceeded(),
        ):
            resp = self.post()
        self.assertEqual(resp.status_code, 413)
        self.assertFalse(File.objects.filter(parent=self.root).exists())

    @override_settings(FILES_DROP_UPLOAD_RATE_TOKEN="2/min")
    def test_the_endpoint_is_throttled_per_token(self):
        self.post(name="a.txt")
        self.post(name="b.txt")
        resp = self.post(name="c.txt")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class ShareLinkUploadTargetTests(APITestCase):
    """``node`` names the folder the visitor is browsing; the file lands there.

    Only a link that lets the visitor see the tree honours it. A drop-only
    link writes to its root whatever the parameter says: answering 204 or
    404 on a guessed uuid would tell a stranger which folders exist.
    """

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.root = File.objects.create(
            owner=self.owner, name="Inbox", node_type=File.NodeType.FOLDER
        )
        self.sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.deep = File.objects.create(
            owner=self.owner,
            name="Deep",
            node_type=File.NodeType.FOLDER,
            parent=self.sub,
        )
        self.elsewhere = File.objects.create(
            owner=self.owner, name="Elsewhere", node_type=File.NodeType.FOLDER
        )
        self.link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.BOTH
        )

    def tearDown(self):
        cache.clear()

    def post(self, node, link=None):
        link = link or self.link
        data = {"file": part()}
        if node is not None:
            data["node"] = node
        return self.client.post(
            f"/api/v1/files/shared/{link.token}/upload", data, format="multipart"
        )

    def test_without_a_node_the_file_lands_in_the_root(self):
        self.assertEqual(self.post(None).status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )

    def test_a_subfolder_node_receives_the_file(self):
        self.assertEqual(
            self.post(str(self.sub.uuid)).status_code, status.HTTP_204_NO_CONTENT
        )
        self.assertTrue(
            File.objects.filter(parent=self.sub, name="report.pdf").exists()
        )
        self.assertFalse(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )

    def test_a_nested_node_receives_the_file(self):
        self.assertEqual(
            self.post(str(self.deep.uuid)).status_code, status.HTTP_204_NO_CONTENT
        )
        self.assertTrue(
            File.objects.filter(parent=self.deep, name="report.pdf").exists()
        )

    def test_the_root_itself_is_a_valid_node(self):
        self.assertEqual(
            self.post(str(self.root.uuid)).status_code, status.HTTP_204_NO_CONTENT
        )
        self.assertTrue(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )

    def test_a_folder_outside_the_link_is_a_404_and_creates_nothing(self):
        resp = self.post(str(self.elsewhere.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(File.objects.filter(name="report.pdf").exists())

    def test_a_trashed_subfolder_is_a_404(self):
        self.sub.deleted_at = timezone.now()
        self.sub.save(update_fields=["deleted_at"])
        self.assertEqual(
            self.post(str(self.sub.uuid)).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertFalse(File.objects.filter(name="report.pdf").exists())

    def test_a_file_node_is_a_404(self):
        doc = File.objects.create(
            owner=self.owner,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            parent=self.root,
        )
        self.assertEqual(
            self.post(str(doc.uuid)).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_a_malformed_node_is_a_404(self):
        self.assertEqual(self.post("not-a-uuid").status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(File.objects.filter(name="report.pdf").exists())

    def test_a_refused_node_does_not_consume_the_count_cap(self):
        self.link.max_file_count = 1
        self.link.save(update_fields=["max_file_count"])
        self.assertEqual(
            self.post(str(self.elsewhere.uuid)).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.post(str(self.sub.uuid)).status_code, status.HTTP_204_NO_CONTENT
        )

    def test_a_drop_only_link_ignores_the_node(self):
        drop = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        self.assertEqual(
            self.post(str(self.sub.uuid), link=drop).status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertTrue(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )
        self.assertFalse(
            File.objects.filter(parent=self.sub, name="report.pdf").exists()
        )

    def test_a_drop_only_link_answers_the_same_for_a_foreign_node(self):
        drop = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        self.assertEqual(
            self.post(str(self.elsewhere.uuid), link=drop).status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertTrue(
            File.objects.filter(parent=self.root, name="report.pdf").exists()
        )
