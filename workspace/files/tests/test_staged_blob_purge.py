"""Blobs a killed worker staged for a new file are cleared once a day old."""

from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase
from django.utils import timezone

from workspace.common.tests.media import IsolatedMediaRootMixin
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

    def test_a_blob_staged_over_a_day_ago_goes(self):
        staged = self._stage()

        purged = self._purge(STAGED_BLOB_TTL + timedelta(minutes=1))

        self.assertEqual(purged, {"purged": 1})
        self.assertFalse(self.storage.exists(staged))

    def test_a_blob_staged_under_a_day_ago_stays(self):
        staged = self._stage()

        purged = self._purge(STAGED_BLOB_TTL - timedelta(minutes=1))

        self.assertEqual(purged, {"purged": 0})
        self.assertTrue(self.storage.exists(staged))

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
