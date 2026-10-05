import io
import os
import shutil
import tempfile
import time
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from workspace.common.storage.facade import BlobStorage
from workspace.common.tests.s3 import S3TestMixin


class CopyBlobsTests(S3TestMixin, SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="workspace-test-media-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        super().setUp()
        self.local = BlobStorage(location=self.root)
        self.bucket_storage = self.make_s3_storage()
        backends = {
            "local": {"backend": "local", "location": self.root},
            "s3": {"backend": "s3", "bucket": self.bucket, **self.s3_connection},
        }
        override = override_settings(BLOB_BACKENDS=backends)
        override.enable()
        self.addCleanup(override.disable)

    def save_local(self, name, data):
        """Save *name* on disk as last written an hour ago, like a blob nobody
        is editing while the copy runs."""
        self.local.save(name, ContentFile(data))
        an_hour_ago = time.time() - 3600
        os.utime(Path(self.root, name), (an_hour_ago, an_hour_ago))

    def copy(self, *args):
        output = io.StringIO()
        call_command("copy_blobs", *args, stdout=output, stderr=io.StringIO())
        return output.getvalue()

    def test_disk_to_bucket_copies_the_blobs_and_nothing_else(self):
        self.local.save("files/users/alice/Docs/a.txt", ContentFile(b"a"))
        self.local.make_dir("files/users/alice/Empty")
        self.local.save("chat/conv/b.png", ContentFile(b"bb"))
        # What MEDIA_ROOT holds besides blobs: the database, the model weights.
        Path(self.root, "db.sqlite3").write_bytes(b"sqlite")
        Path(self.root, "models").mkdir()
        Path(self.root, "models", "weights.onnx").write_bytes(b"weights")

        output = self.copy("--to", "s3")

        keys = self.keys()
        self.assertIn("files/users/alice/Docs/a.txt", keys)
        self.assertIn("files/users/alice/Empty/", keys)
        self.assertIn("chat/conv/b.png", keys)
        self.assertFalse([key for key in keys if key.startswith(("db.", "models"))])
        self.assertIn("Copied 2 blob(s), 3 bytes, from local to s3", output)

    def test_a_second_run_copies_nothing_again(self):
        self.save_local("files/users/alice/a.txt", b"a")
        self.copy("--to", "s3")

        output = self.copy("--to", "s3")

        self.assertIn("Copied 0 blob(s)", output)
        self.assertIn("1 already there", output)

    def test_a_blob_that_changed_size_is_copied_again(self):
        self.local.save("files/users/alice/a.txt", ContentFile(b"a"))
        self.copy("--to", "s3")
        self.local.delete("files/users/alice/a.txt")
        self.local.save("files/users/alice/a.txt", ContentFile(b"longer"))

        self.copy("--to", "s3")

        with self.bucket_storage.open("files/users/alice/a.txt") as handle:
            self.assertEqual(handle.read(), b"longer")

    def test_an_edit_that_kept_the_size_is_copied_back(self):
        """Back to the disk after a while on the bucket: the stale copy left
        on disk is the same size as the edited blob, and older."""
        self.save_local("files/users/alice/a.txt", b"draft")
        self.copy("--to", "s3")
        self.bucket_storage.replace("files/users/alice/a.txt", ContentFile(b"final"))

        self.copy("--to", "local")

        with self.local.open("files/users/alice/a.txt") as handle:
            self.assertEqual(handle.read(), b"final")

    def test_bucket_to_disk(self):
        self.bucket_storage.save("files/users/alice/a.txt", ContentFile(b"a"))
        self.bucket_storage.make_dir("files/users/alice/Empty")

        self.copy("--to", "local")

        with self.local.open("files/users/alice/a.txt") as handle:
            self.assertEqual(handle.read(), b"a")
        self.assertTrue(self.local.is_dir("files/users/alice/Empty"))

    def test_a_dry_run_writes_nothing(self):
        self.local.save("files/users/alice/a.txt", ContentFile(b"a"))

        output = self.copy("--to", "s3", "--dry-run")

        self.assertEqual(self.keys(), [])
        self.assertIn("Would copy 1 blob(s)", output)

    def test_a_backend_that_is_not_configured(self):
        with (
            override_settings(BLOB_BACKENDS={"local": {"backend": "local"}}),
            self.assertRaises(CommandError),
        ):
            self.copy("--to", "s3")
