"""WebDAV writes honour the share permission, like the REST API does.

A read-only share allows reading only, a read-write share allows replacing a
file's content, and creating, renaming, moving or deleting needs edit rights
(owner or group member).
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File, FileShare
from workspace.files.services import FileService
from workspace.files.tests import test_webdav

User = get_user_model()

DAV = "http://testserver/dav"


class WebDAVSharePermissionTests(TestCase):
    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        # A fresh app per test: wsgidav keeps DAV locks in memory, and a lock
        # taken by one test would answer 423 to the next one on that path.
        self._app = create_webdav_app()
        self.user = User.objects.create_user(username="davguest", password="pass123")
        self.owner = User.objects.create_user(username="davowner", password="pass123")
        self.auth = test_webdav._basic_auth_header("davguest", "pass123")

    def _share(self, file_obj, permission):
        FileShare.objects.create(
            file=file_obj,
            shared_by=self.owner,
            shared_with=self.user,
            permission=permission,
        )
        return file_obj

    def _owned_file(self, name, body=b"theirs", parent=None):
        return FileService.create_file(
            self.owner,
            name,
            parent=parent,
            content=ContentFile(body, name=name),
            mime_type="text/plain",
        )

    def _body(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as f:
            return f.read()

    def _read_only_folder(self):
        folder = FileService.create_folder(self.owner, "Shared")
        self._owned_file("doc.txt", parent=folder)
        return self._share(folder, FileShare.Permission.READ_ONLY)

    # ── a file shared read-only ──

    def test_put_over_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request("PUT", "/theirs.txt", body=b"overwritten")

        self.assertEqual(code, 403)
        self.assertEqual(self._body(shared), b"theirs")

    def test_save_by_rename_over_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )
        self._request("PUT", "/.theirs.txt.tmp", body=b"overwritten")

        code, _, _ = self._request(
            "MOVE", "/.theirs.txt.tmp", headers={"Destination": f"{DAV}/theirs.txt"}
        )

        self.assertEqual(code, 403)
        self.assertEqual(self._body(shared), b"theirs")
        self.assertIsNone(shared.deleted_at)

    def test_delete_of_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request("DELETE", "/theirs.txt")

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertIsNone(shared.deleted_at)

    def test_rename_of_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request(
            "MOVE", "/theirs.txt", headers={"Destination": f"{DAV}/renamed.txt"}
        )

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertEqual(shared.name, "theirs.txt")

    def test_lock_of_a_read_only_file_is_refused(self):
        self._share(self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY)

        code, _, _ = self._request(
            "LOCK",
            "/theirs.txt",
            body=(
                b'<?xml version="1.0" encoding="utf-8"?><lockinfo xmlns="DAV:">'
                b"<lockscope><exclusive/></lockscope><locktype><write/></locktype>"
                b"</lockinfo>"
            ),
            headers={"Content-Type": "application/xml"},
        )

        self.assertEqual(code, 403)

    def test_a_read_only_file_stays_readable(self):
        self._share(self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY)

        code, _, body = self._request("GET", "/theirs.txt")

        self.assertEqual(code, 200)
        self.assertEqual(body, b"theirs")

    # ── a file shared read-write ──

    def test_put_over_a_read_write_file_replaces_its_content(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )

        code, _, _ = self._request("PUT", "/theirs.txt", body=b"edited")

        self.assertIn(code, (200, 204))
        self.assertEqual(self._body(shared), b"edited")

    def test_save_by_rename_over_a_read_write_file_replaces_its_content(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )
        self._request("PUT", "/.theirs.txt.tmp", body=b"edited")

        code, _, _ = self._request(
            "MOVE", "/.theirs.txt.tmp", headers={"Destination": f"{DAV}/theirs.txt"}
        )

        self.assertLess(code, 300)
        self.assertEqual(self._body(shared), b"edited")

    def test_delete_of_a_read_write_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )

        code, _, _ = self._request("DELETE", "/theirs.txt")

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertIsNone(shared.deleted_at)

    # ── a folder shared read-only ──

    def test_put_of_a_new_file_into_a_read_only_folder_is_refused(self):
        folder = self._read_only_folder()

        code, _, _ = self._request("PUT", "/Shared/new.txt", body=b"new")

        self.assertEqual(code, 403)
        self.assertFalse(File.objects.filter(parent=folder, name="new.txt").exists())

    def test_mkcol_in_a_read_only_folder_is_refused(self):
        folder = self._read_only_folder()

        code, _, _ = self._request("MKCOL", "/Shared/sub")

        self.assertEqual(code, 403)
        self.assertFalse(File.objects.filter(parent=folder, name="sub").exists())

    def test_copy_into_a_read_only_folder_is_refused(self):
        folder = self._read_only_folder()
        self._request("PUT", "/mine.txt", body=b"mine")

        code, _, _ = self._request(
            "COPY", "/mine.txt", headers={"Destination": f"{DAV}/Shared/mine.txt"}
        )

        self.assertEqual(code, 403)
        self.assertFalse(File.objects.filter(parent=folder, name="mine.txt").exists())

    def test_move_into_a_read_only_folder_is_refused(self):
        folder = self._read_only_folder()
        self._request("PUT", "/mine.txt", body=b"mine")

        code, _, _ = self._request(
            "MOVE", "/mine.txt", headers={"Destination": f"{DAV}/Shared/mine.txt"}
        )

        self.assertEqual(code, 403)
        self.assertFalse(File.objects.filter(parent=folder, name="mine.txt").exists())
        mine = File.objects.get(owner=self.user, name="mine.txt")
        self.assertIsNone(mine.parent_id)
        self.assertIsNone(mine.deleted_at)

    def test_delete_of_a_read_only_folder_is_refused(self):
        folder = self._read_only_folder()

        code, _, _ = self._request("DELETE", "/Shared")

        self.assertEqual(code, 403)
        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)

    def test_lock_of_a_read_only_folder_is_refused(self):
        self._read_only_folder()

        code, _, _ = self._request(
            "LOCK",
            "/Shared",
            body=(
                b'<?xml version="1.0" encoding="utf-8"?><lockinfo xmlns="DAV:">'
                b"<lockscope><exclusive/></lockscope><locktype><write/></locktype>"
                b"</lockinfo>"
            ),
            headers={"Content-Type": "application/xml", "Depth": "infinity"},
        )

        self.assertEqual(code, 403)

    # ── a destination folder that does not resolve ──

    def test_copy_into_an_unknown_folder_is_refused(self):
        self._request("PUT", "/mine.txt", body=b"mine")

        code, _, _ = self._request(
            "COPY", "/mine.txt", headers={"Destination": f"{DAV}/Nowhere/mine.txt"}
        )

        self.assertEqual(code, 409)
        self.assertEqual(
            File.objects.filter(owner=self.user, name="mine.txt").count(), 1
        )

    def test_move_into_an_unknown_folder_is_refused(self):
        self._request("PUT", "/mine.txt", body=b"mine")

        code, _, _ = self._request(
            "MOVE", "/mine.txt", headers={"Destination": f"{DAV}/Nowhere/mine.txt"}
        )

        self.assertEqual(code, 409)
        mine = File.objects.get(owner=self.user, name="mine.txt")
        self.assertIsNone(mine.parent_id)

    # ── the user's own files are unaffected ──

    def test_own_files_can_be_written_renamed_and_deleted(self):
        self.assertEqual(self._request("MKCOL", "/Mine")[0], 201)
        self.assertEqual(self._request("PUT", "/Mine/a.txt", body=b"a")[0], 201)
        self.assertEqual(self._request("PUT", "/Mine/a.txt", body=b"b")[0], 204)
        code, _, _ = self._request(
            "MOVE", "/Mine/a.txt", headers={"Destination": f"{DAV}/Mine/b.txt"}
        )
        self.assertEqual(code, 201)
        self.assertEqual(self._request("DELETE", "/Mine/b.txt")[0], 204)
