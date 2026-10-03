"""The files module end to end on object storage (an S3 bucket, moto).

Every operation a user can run on the tree - upload, read, rename, move,
trash, restore, copy, delete, replace, a WebDAV PUT and GET - must leave the
bucket holding exactly what a disk would: one key per blob at its node's tree
path, and a marker for each folder kept empty.
"""

import base64
import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import TestCase

from workspace.common.tests.s3 import S3StoragesMixin
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService

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

        FileService.rename(docs, "Archive")

        self.assertEqual(
            self.keys(),
            ["files/users/alice/Archive/", "files/users/alice/Archive/a.txt"],
        )
        self.assertEqual(self.read(f), b"a")

    def test_moving_a_folder_moves_its_blobs(self):
        docs = FileService.create_folder(self.user, "Docs")
        archive = FileService.create_folder(self.user, "Archive")
        f = self.upload("a.txt", b"a", parent=docs)

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

        FileService.rename(f, "b.txt")
        FileService.move(f, archive)

        self.assertEqual(
            self.keys(),
            ["files/users/alice/Archive/", "files/users/alice/Archive/b.txt"],
        )
        self.assertEqual(self.read(f), b"a")

    def test_trash_then_restore(self):
        docs = FileService.create_folder(self.user, "Docs")
        f = self.upload("a.txt", b"a", parent=docs)

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
