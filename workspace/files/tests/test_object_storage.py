"""The files module end to end on object storage (an S3 bucket, moto).

Every operation a user can run on the tree - upload, read, rename, move,
trash, restore, copy, delete, replace, a WebDAV PUT and GET - must leave the
bucket holding exactly what a disk would: one key per blob at its node's tree
path, and a marker for each folder kept empty.

A move copies before it deletes, and the delete waits for the commit of the
rows that point at the copies: the tests that move run their on-commit
callbacks, as a request's commit would.
"""

import base64
import io
from unittest import mock

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.test import TestCase
from wsgidav.dav_error import DAVError

from workspace.common.storage.s3 import MIN_PART_SIZE
from workspace.common.tests.s3 import S3StoragesMixin
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService
from workspace.files.webdav.resources import FileResource

User = get_user_model()


class ObjectStorageFilesTests(S3StoragesMixin, TestCase):
    def setUp(self):
        super().setUp()
        # File.content resolved its storage when the model was imported.
        patcher = mock.patch.object(
            File._meta.get_field("content"), "storage", storages["files"]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(username="alice", password="pw")

    def upload(self, name, data, parent=None):
        return FileService.create_file(
            self.user, name, parent=parent, content=ContentFile(data, name=name)
        )

    def read(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as handle:
            return handle.read()

    def test_an_upload_lands_at_its_tree_path(self):
        f = self.upload("doc.txt", b"hello")

        self.assertEqual(f.content.name, "files/users/alice/doc.txt")
        self.assertEqual(self.keys(), ["files/users/alice/doc.txt"])
        self.assertEqual(self.read(f), b"hello")

    def test_an_empty_folder_is_kept(self):
        FileService.create_folder(self.user, "Docs")

        self.assertEqual(self.keys(), ["files/users/alice/Docs/"])

    def test_renaming_a_folder_moves_its_blobs(self):
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"a", parent=docs)

        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(docs, "Archive")

        self.assertEqual(
            self.keys(),
            ["files/users/alice/Archive/", "files/users/alice/Archive/a.txt"],
        )
        self.assertEqual(self.read(f), b"a")

    def test_a_folder_rename_that_cannot_move_its_blobs_changes_nothing(self):
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"a", parent=docs)
        self.upload("b.txt", b"b", parent=docs)
        before = self.keys()
        backend = storages["default"].backend
        real_copy = backend._copy

        def copy_once(*args):
            if copy_once.calls:
                raise OSError("the store went away")
            copy_once.calls += 1
            return real_copy(*args)

        copy_once.calls = 0
        with (
            mock.patch.object(backend, "_copy", copy_once),
            self.assertRaises(OSError),
        ):
            FileService.rename(docs, "Archive")

        docs.refresh_from_db()
        self.assertEqual(docs.name, "Docs")
        self.assertEqual(self.keys(), before)
        self.assertEqual(self.read(f), b"a")

    def test_a_rename_rolled_back_after_the_move_keeps_the_originals(self):
        """The rows go back to the originals, which are still there: the
        copies the move made are left as duplicates, never as a gap."""
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"a", parent=docs)

        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            FileService.rename(docs, "Archive")
            # The request fails after the move: its transaction rolls back.
            transaction.set_rollback(True)

        docs.refresh_from_db()
        self.assertEqual(docs.name, "Docs")
        self.assertEqual(self.read(f), b"a")
        self.assertIn("files/users/alice/Docs/a.txt", self.keys())

    def test_moving_a_folder_moves_its_blobs(self):
        docs = FileService.create_folder(self.user, "Docs")
        archive = FileService.create_folder(self.user, "Archive")
        f = self.upload("a.txt", b"a", parent=docs)

        with self.captureOnCommitCallbacks(execute=True):
            FileService.move(docs, archive)

        self.assertEqual(
            self.keys(),
            [
                "files/users/alice/Archive/",
                "files/users/alice/Archive/Docs/",
                "files/users/alice/Archive/Docs/a.txt",
            ],
        )
        self.assertEqual(self.read(f), b"a")

    def test_renaming_then_moving_a_file(self):
        archive = FileService.create_folder(self.user, "Archive")
        f = self.upload("a.txt", b"a")

        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(f, "b.txt")
            FileService.move(f, archive)

        self.assertEqual(
            self.keys(),
            ["files/users/alice/Archive/", "files/users/alice/Archive/b.txt"],
        )
        self.assertEqual(self.read(f), b"a")

    def _row_during_copy(self, file_obj, move):
        """Where *file_obj*'s row points while *move* copies its blob."""
        backend = storages["default"].backend
        real_copy = backend._copy
        seen = []

        def copy(*args):
            seen.append(File.objects.get(pk=file_obj.pk).content.name)
            return real_copy(*args)

        with (
            self.captureOnCommitCallbacks(execute=True),
            mock.patch.object(backend, "_copy", copy),
        ):
            move()
        return seen

    def test_a_move_claims_the_rows_before_it_copies(self):
        """A content write claims its row before writing a byte. A move that
        claims the rows first holds such a write back until it commits; one
        let through mid-copy would land on a source after its copy was taken,
        and go with that source when the move commits."""
        docs = FileService.create_folder(self.user, "Docs")
        archive = FileService.create_folder(self.user, "Archive")
        f = self.upload("a.txt", b"a", parent=docs)
        cases = [
            (
                "folder rename",
                lambda: FileService.rename(docs, "Papers"),
                "Papers/a.txt",
            ),
            (
                "folder move",
                lambda: FileService.move(docs, archive),
                "Archive/Papers/a.txt",
            ),
            (
                "file rename",
                lambda: FileService.rename(f, "b.txt"),
                "Archive/Papers/b.txt",
            ),
            ("file move", lambda: FileService.move(f, None), "b.txt"),
        ]
        for label, move, destination in cases:
            with self.subTest(label):
                for node in (docs, archive, f):
                    node.refresh_from_db()
                seen = self._row_during_copy(f, move)

                self.assertEqual(seen, [f"files/users/alice/{destination}"])
        self.assertEqual(self.read(f), b"a")

    def test_a_save_that_waited_on_a_folder_move_follows_it(self):
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"old", parent=docs)
        loaded = File.objects.get(pk=f.pk)
        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(docs, "Archive")

        FileService.update_content(
            loaded, ContentFile(b"new", name="a.txt"), acting_user=self.user
        )

        f.refresh_from_db()
        self.assertEqual(f.content.name, "files/users/alice/Archive/a.txt")
        self.assertEqual(self.read(f), b"new")
        self.assertEqual(
            self.keys(),
            ["files/users/alice/Archive/", "files/users/alice/Archive/a.txt"],
        )

    def test_a_save_that_waited_on_a_rename_follows_it(self):
        f = self.upload("a.txt", b"old")
        loaded = File.objects.get(pk=f.pk)
        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(f, "b.txt")

        FileService.update_content(
            loaded, ContentFile(b"new", name="a.txt"), acting_user=self.user
        )

        f.refresh_from_db()
        self.assertEqual(f.content.name, "files/users/alice/b.txt")
        self.assertEqual(self.read(f), b"new")
        self.assertEqual(self.keys(), ["files/users/alice/b.txt"])

    def test_trash_then_restore(self):
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"a", parent=docs)

        with self.captureOnCommitCallbacks(execute=True):
            FileService.soft_delete(docs)
        self.assertEqual(
            self.keys(),
            [
                f"trash/users/alice/{docs.uuid}/Docs/",
                f"trash/users/alice/{docs.uuid}/Docs/a.txt",
            ],
        )
        self.assertEqual(self.read(f), b"a")

        docs.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            FileService.restore(docs)
        self.assertEqual(
            self.keys(), ["files/users/alice/Docs/", "files/users/alice/Docs/a.txt"]
        )
        self.assertEqual(self.read(f), b"a")

    def test_copying_a_folder(self):
        docs = FileService.create_folder(self.user, "Docs")
        self.upload("a.txt", b"a", parent=docs)

        copied = FileService.copy(docs, None, self.user)

        copied_file = File.objects.get(parent=copied, name="a.txt")
        self.assertEqual(self.read(copied_file), b"a")
        self.assertIn("files/users/alice/Docs (Copy)/a.txt", self.keys())
        self.assertIn("files/users/alice/Docs/a.txt", self.keys())

    def test_a_hard_delete_removes_the_blobs(self):
        docs = FileService.create_folder(self.user, "Docs")
        self.upload("a.txt", b"a", parent=docs)

        FileService.hard_delete(docs)

        self.assertEqual(self.keys(), [])

    def test_replacing_the_content(self):
        f = self.upload("doc.txt", b"old")

        FileService.update_content(
            f, ContentFile(b"new", name="doc.txt"), name="doc.txt"
        )

        self.assertEqual(self.keys(), ["files/users/alice/doc.txt"])
        self.assertEqual(self.read(f), b"new")

    def test_the_sync_adopts_an_object_dropped_into_the_bucket(self):
        self.s3.put_object(
            Bucket=self.bucket, Key="files/users/alice/dropped.txt", Body=b"x"
        )

        result = FileSyncService().sync_folder_shallow(self.user)

        self.assertEqual(result.files_created, 1)
        self.assertEqual(self.read(File.objects.get(name="dropped.txt")), b"x")


class ObjectStorageWebDavTests(S3StoragesMixin, TestCase):
    # The smallest part S3 allows, so a large PUT is a multipart upload.
    s3_options = {"part_size": MIN_PART_SIZE}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from workspace.files.webdav.app import create_webdav_app

        cls._app = create_webdav_app()

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(
            File._meta.get_field("content"), "storage", storages["files"]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(username="davs3", password="pass123")

    def dav(self, method, path, body=b""):
        credentials = base64.b64encode(b"davs3:pass123").decode()
        environ = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "/dav",
            "PATH_INFO": path,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "testserver",
            "HTTP_AUTHORIZATION": f"Basic {credentials}",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.BytesIO(),
            "wsgi.url_scheme": "http",
            "CONTENT_LENGTH": str(len(body)),
        }
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = int(status.split(" ", 1)[0])

        result = self._app(environ, start_response)
        payload = b"".join(result)
        if hasattr(result, "close"):
            result.close()
        return captured["status"], payload

    def test_put_then_get(self):
        status, _ = self.dav("PUT", "/notes.txt", b"through webdav")
        self.assertEqual(status, 201)

        status, body = self.dav("GET", "/notes.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"through webdav")
        self.assertEqual(self.keys(), ["files/users/davs3/notes.txt"])

    def test_an_overwrite_replaces_the_blob(self):
        self.dav("PUT", "/notes.txt", b"first")

        status, _ = self.dav("PUT", "/notes.txt", b"second version")
        self.assertIn(status, (200, 204))

        _, body = self.dav("GET", "/notes.txt")
        self.assertEqual(body, b"second version")
        self.assertEqual(self.keys(), ["files/users/davs3/notes.txt"])
        self.assertEqual(
            self.s3.list_multipart_uploads(Bucket=self.bucket).get("Uploads", []), []
        )

    def test_an_upload_the_file_was_moved_under_is_refused(self):
        """Its bytes are staged under the path the file had when the PUT began,
        which the move empties once it commits."""
        docs = FileService.create_folder(self.user, "Docs")
        f = FileService.create_file(
            self.user, "a.txt", parent=docs, content=ContentFile(b"old", name="a.txt")
        )
        environ = {
            "REQUEST_METHOD": "PUT",
            "SCRIPT_NAME": "",
            "PATH_INFO": "/Docs/a.txt",
            "wsgi.input": io.BytesIO(b""),
            "wsgidav.provider": None,
            "workspace.user": self.user,
        }
        resource = FileResource("/Docs/a.txt", environ, f)
        buf = resource.begin_write()
        buf.write(b"new")
        buf.close()
        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(docs, "Archive")

        with self.assertRaises(DAVError) as refused:
            resource.end_write(with_errors=False)

        self.assertEqual(refused.exception.value, 409)
        f.refresh_from_db()
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"old")
        self.assertEqual(
            self.keys(),
            ["files/users/davs3/Archive/", "files/users/davs3/Archive/a.txt"],
        )

    def test_an_overwrite_the_store_fails_to_complete_keeps_nothing_open(self):
        """wsgidav calls end_write once: an upload the store refused to
        complete would stay open in the bucket, holding its parts."""
        self.dav("PUT", "/notes.txt", b"first")
        refused = ClientError(
            {
                "Error": {"Code": "InternalError"},
                "ResponseMetadata": {"HTTPStatusCode": 500},
            },
            "CompleteMultipartUpload",
        )

        with mock.patch.object(
            storages["files"].backend.client,
            "complete_multipart_upload",
            side_effect=refused,
        ):
            status, _ = self.dav("PUT", "/notes.txt", b"x" * (MIN_PART_SIZE + 1))

        self.assertEqual(status, 500)
        self.assertEqual(
            self.s3.list_multipart_uploads(Bucket=self.bucket).get("Uploads", []), []
        )
        _, body = self.dav("GET", "/notes.txt")
        self.assertEqual(body, b"first")
