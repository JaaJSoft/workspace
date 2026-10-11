"""Upload spool files a killed worker left on the data volume are cleared."""

import os
import shutil
import tempfile
import time

from django.test import SimpleTestCase, override_settings

from workspace.core.tasks import STALE_UPLOAD_SECONDS, purge_stale_uploads


class PurgeStaleUploadsTests(SimpleTestCase):
    def setUp(self):
        self.spool_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.spool_dir, ignore_errors=True)
        override = override_settings(FILE_UPLOAD_TEMP_DIR=self.spool_dir)
        override.enable()
        self.addCleanup(override.disable)

    def _touch(self, name, age):
        path = os.path.join(self.spool_dir, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_an_abandoned_upload_goes_and_one_in_progress_stays(self):
        abandoned = self._touch("tmpab12cd.upload.mov", STALE_UPLOAD_SECONDS + 60)
        in_progress = self._touch("tmpef34gh.upload.mov", 60)

        self.assertEqual(purge_stale_uploads(), 1)
        self.assertFalse(os.path.exists(abandoned))
        self.assertTrue(os.path.exists(in_progress))

    def test_a_file_that_is_not_an_upload_spool_is_left_alone(self):
        other = self._touch("celerybeat-schedule", STALE_UPLOAD_SECONDS + 60)

        self.assertEqual(purge_stale_uploads(), 0)
        self.assertTrue(os.path.exists(other))

    def test_a_spool_directory_never_created_has_nothing_to_purge(self):
        absent = os.path.join(self.spool_dir, "absent")
        with override_settings(FILE_UPLOAD_TEMP_DIR=absent):
            self.assertEqual(purge_stale_uploads(), 0)
