import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from workspace.core.services.admin_dashboard import quarantined_file_count
from workspace.files.models import File, FileScan
from workspace.files.services.scanning import override
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked
from workspace.files.services.thumbnails.generation import (
    generate_missing_thumbnails,
)

User = get_user_model()

BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}


@override_settings(**BLOCKING)
class OverridePolicyTests(TestCase):
    """An administrator's "this one is fine" vouches for bytes, not for a row."""

    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="p")
        self.file = File.objects.create(
            owner=self.admin,
            name="a.txt",
            node_type=File.NodeType.FILE,
            content_hash="h1",
        )
        self.scan = FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Test.Sig",
            content_hash="h1",
            scanned_at=timezone.now(),
        )

    def _override(self, **fields):
        FileScan.objects.filter(pk=self.scan.pk).update(
            overridden_at=timezone.now(), overridden_by=self.admin, **fields
        )
        self.file.refresh_from_db()

    def test_override_on_the_current_bytes_lifts_the_block(self):
        self._override()
        self.assertFalse(is_blocked(self.file))

    def test_override_does_not_survive_a_content_replacement(self):
        self._override()
        File.objects.filter(pk=self.file.pk).update(content_hash="h2")
        self.file.refresh_from_db()
        self.assertTrue(is_blocked(self.file))

    def test_override_with_a_blank_verdict_hash_does_not_apply(self):
        self._override(content_hash="")
        self.assertTrue(is_blocked(self.file))

    def test_override_with_a_blank_file_hash_does_not_apply(self):
        self._override(content_hash="")
        File.objects.filter(pk=self.file.pk).update(content_hash="")
        self.file.refresh_from_db()
        self.assertTrue(is_blocked(self.file))

    def test_overridden_file_comes_back_into_file_querysets(self):
        self._override()
        self.assertIn(self.file, exclude_blocked(File.objects.all()))

    def test_a_stale_override_keeps_the_file_out_of_file_querysets(self):
        self._override()
        File.objects.filter(pk=self.file.pk).update(content_hash="h2")
        self.assertNotIn(self.file, exclude_blocked(File.objects.all()))

    def test_overridden_file_stops_counting_on_the_admin_dashboard(self):
        self._override()
        request = RequestFactory().get("/admin/")
        self.assertEqual(quarantined_file_count(request), 0)

    def test_rescanning_the_same_bytes_does_not_revoke_the_override(self):
        """The verdict is rewritten, the decision about it is not.

        Adding the override columns to scan_file's ``defaults`` would break
        this - and would make the admin action pointless, since the next
        scan_files pass re-blocks everything it just cleared.
        """
        self._override()
        FileScan.objects.update_or_create(
            file=self.file,
            defaults={
                "status": FileScan.Status.INFECTED,
                "signature": "Test.Sig",
                "content_hash": "h1",
                "scanned_at": timezone.now(),
            },
        )
        self.file.refresh_from_db()
        self.assertFalse(is_blocked(self.file))


@override_settings(**BLOCKING)
class MarkSafeTests(TestCase):
    """The service behind the admin action, and what it restores."""

    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="p")
        self.file = File.objects.create(
            owner=self.admin,
            name="a.png",
            node_type=File.NodeType.FILE,
            type="png",
            content_hash="h1",
        )
        self.scan = FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Test.Sig",
            content_hash="h1",
            scanned_at=timezone.now(),
        )

    def _mark_safe(self, **kwargs):
        from workspace.files.services.scanning.override import mark_safe

        with (
            patch("workspace.files.services.search_index.index_file") as self.index,
            patch(
                "workspace.files.services.thumbnails.generation.generate_thumbnail",
                return_value=True,
            ) as self.thumbnail,
        ):
            return mark_safe(self.scan, user=self.admin, **kwargs)

    def test_it_records_who_lifted_the_block_and_why(self):
        self._mark_safe(reason="Signature retired upstream")
        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.overridden_at)
        self.assertEqual(self.scan.overridden_by, self.admin)
        self.assertEqual(self.scan.override_reason, "Signature retired upstream")

    def test_it_keeps_the_verdict_it_overrides(self):
        self._mark_safe()
        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, FileScan.Status.INFECTED)
        self.assertEqual(self.scan.signature, "Test.Sig")

    def test_it_lifts_the_block(self):
        self._mark_safe()
        self.file.refresh_from_db()
        self.assertFalse(is_blocked(self.file))

    def test_it_puts_the_search_document_back(self):
        self._mark_safe()
        self.index.assert_called_once()

    def test_it_regenerates_the_thumbnail_quarantining_deleted(self):
        self._mark_safe()
        self.thumbnail.assert_called_once()
        self.file.refresh_from_db()
        self.assertTrue(self.file.has_thumbnail)

    def test_it_reports_that_nothing_was_blocked_when_the_verdict_is_clean(self):
        FileScan.objects.filter(pk=self.scan.pk).update(status=FileScan.Status.CLEAN)
        self.scan.refresh_from_db()
        self.assertEqual(self._mark_safe(), override.NOT_BLOCKED)
        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)

    def test_it_reports_that_it_lifted_a_block(self):
        self.assertEqual(self._mark_safe(), override.LIFTED)

    def test_it_refuses_a_verdict_about_bytes_the_file_no_longer_holds(self):
        """Pinning to a hash the file has moved past yields a dead override.

        The right move is a re-scan, which writes a verdict for the current
        bytes that can then be overridden - so refuse rather than record
        something that can never take effect.
        """
        File.objects.filter(pk=self.file.pk).update(content_hash="h2")
        self.scan.refresh_from_db()

        self.assertEqual(self._mark_safe(), override.UNPINNABLE)

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)

    def test_it_refuses_a_file_with_no_recorded_content_hash(self):
        """An override with nothing to pin itself to would never apply.

        Silently writing one would leave the file blocked behind a success
        message, so the caller is told to backfill the hashes instead.
        """
        FileScan.objects.filter(pk=self.scan.pk).update(content_hash="")
        File.objects.filter(pk=self.file.pk).update(content_hash="")
        self.scan.refresh_from_db()

        self.assertEqual(self._mark_safe(), override.UNPINNABLE)

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)
        self.file.refresh_from_db()
        self.assertTrue(is_blocked(self.file))


@override_settings(**BLOCKING)
class ThumbnailBackfillTests(TestCase):
    """The hourly backfill must not undo a quarantine.

    scan_file deletes the thumbnail of a file it blocks, but the backfill
    selects on has_thumbnail=False alone - so before the fix the preview of an
    infected image came back at the next pass, an hour later.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="thumb", password="p")

    def _image(self, name="a.jpg"):
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 120, 200)).save(buf, format="JPEG")
        f = File(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            type="jpeg",
            content_hash="h1",
        )
        f.content = ContentFile(buf.getvalue(), name=name)
        f.size = buf.tell()
        f.save()
        return f

    def _infect(self, file_obj, **fields):
        return FileScan.objects.create(
            file=file_obj,
            status=FileScan.Status.INFECTED,
            signature="Test.Sig",
            content_hash="h1",
            scanned_at=timezone.now(),
            **fields,
        )

    def test_quarantined_image_is_skipped(self):
        self._infect(self._image())
        self.assertEqual(generate_missing_thumbnails()["total"], 0)

    def test_readable_image_is_still_picked_up(self):
        self._image()
        self.assertEqual(generate_missing_thumbnails()["generated"], 1)

    def test_overridden_image_is_picked_up_again(self):
        self._infect(
            self._image(), overridden_at=timezone.now(), overridden_by=self.user
        )
        self.assertEqual(generate_missing_thumbnails()["generated"], 1)
