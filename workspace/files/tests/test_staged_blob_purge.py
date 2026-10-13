"""What a killed worker staged is cleared once a day old."""

import os
from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import SimpleTestCase
from django.utils import timezone

from workspace.common.tests.media import IsolatedMediaRootMixin, local_storage_only
from workspace.files.models import File
from workspace.files.services._names import _staged
from workspace.files.tasks import STAGED_BLOB_TTL, purge_staged_blobs


class PurgeStagedBlobsTests(IsolatedMediaRootMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.storage = File._meta.get_field("content").storage

    def _stage(self):
        """Stage a blob and never leave ``_staged``, like a worker killed inside it."""
        return self.enterContext(_staged(ContentFile(b"bytes"))).name

    def _purge(self, later):
        """Run the purge as it would run *later* from now."""
        with patch("django.utils.timezone.now", return_value=timezone.now() + later):
            return purge_staged_blobs.run()

    # Object storage stages nothing for a new file: it writes the bytes in the
    # transaction that inserts the row.
    @local_storage_only
    def test_a_blob_staged_over_a_day_ago_goes(self):
        staged = self._stage()

        purged = self._purge(STAGED_BLOB_TTL + timedelta(minutes=1))

        self.assertEqual(purged, {"purged": 1})
        self.assertFalse(self.storage.exists(staged))

    @local_storage_only
    def test_a_blob_staged_under_a_day_ago_stays(self):
        staged = self._stage()

        purged = self._purge(STAGED_BLOB_TTL - timedelta(minutes=1))

        self.assertEqual(purged, {"purged": 0})
        self.assertTrue(self.storage.exists(staged))

    @local_storage_only
    def test_a_blob_moved_into_place_after_the_listing_is_skipped(self):
        self._stage()
        modified_time = self.storage.get_modified_time

        def moved_since_listed(name):
            self.storage.delete(name)
            return modified_time(name)

        with patch.object(
            self.storage, "get_modified_time", side_effect=moved_since_listed
        ):
            purged = self._purge(STAGED_BLOB_TTL + timedelta(minutes=1))

        self.assertEqual(purged, {"purged": 0})

    def test_a_staging_area_never_created_has_nothing_to_purge(self):
        self.assertEqual(purge_staged_blobs.run(), {"purged": 0})

    @local_storage_only
    def test_an_upload_left_uncommitted_goes_too(self):
        storage = storages["files"]
        abandoned = storage.staged_writer("files/users/alice/old.bin")
        abandoned.write(b"old")
        abandoned._close()
        two_days_ago = (timezone.now() - timedelta(days=2)).timestamp()
        os.utime(abandoned._temp_path, (two_days_ago, two_days_ago))
        live = storage.staged_writer("files/users/alice/new.bin")
        live.write(b"new")

        purged = purge_staged_blobs.run()

        self.assertEqual(purged, {"purged": 1})
        self.assertFalse(os.path.exists(abandoned._temp_path))
        live.commit()
        self.assertTrue(storage.exists("files/users/alice/new.bin"))
