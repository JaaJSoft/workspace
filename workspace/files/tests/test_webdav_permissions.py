"""WebDAV writes honour the share permission, like the REST API does.

A read-only share allows reading only, a read-write share allows replacing a
file's content, and creating, renaming, moving or deleting needs edit rights
(owner or group member). A shared file is served in ``/Shared``; a folder is
never shared with someone, and a share row on one opens nothing.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from wsgidav.dav_error import HTTP_CONFLICT, DAVError

from workspace.common.dav.auth import clear_auth_cache
from workspace.common.tests.wsgi import served_in_test
from workspace.files.models import File, FileShare
from workspace.files.services import tree
from workspace.files.tests import test_webdav
from workspace.files.tests.test_webdav import DAV, DAV_HOME, HOME
from workspace.files.webdav.namespace import SHARED
from workspace.files.webdav.resources import FileResource

User = get_user_model()

THEIRS = f"/{SHARED}/theirs.txt"


class WebDAVSharePermissionTests(TestCase):
    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        # A fresh app per test: wsgidav keeps DAV locks in memory, and a lock
        # taken by one test would answer 423 to the next one on that path.
        self._app = served_in_test(create_webdav_app())
        # DAV logins are cached per username and password, which tests reuse:
        # a cached login from an earlier test names a user it rolled back.
        clear_auth_cache()
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
        return tree.create_file(
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

    def _folder_shared_by_hand(self):
        """A share row no endpoint writes: sharing with someone is file-only."""
        folder = tree.create_folder(self.owner, "Theirs")
        self._owned_file("doc.txt", parent=folder)
        return self._share(folder, FileShare.Permission.READ_WRITE)

    # ── a file shared read-only ──

    def test_put_over_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request("PUT", THEIRS, body=b"overwritten")

        self.assertEqual(code, 403)
        self.assertEqual(self._body(shared), b"theirs")

    def test_save_by_rename_over_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )
        self._request("PUT", f"{HOME}/.theirs.txt.tmp", body=b"overwritten")

        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/.theirs.txt.tmp",
            headers={"Destination": f"{DAV}{THEIRS}"},
        )

        self.assertEqual(code, 403)
        self.assertEqual(self._body(shared), b"theirs")
        self.assertIsNone(shared.deleted_at)

    def test_delete_of_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request("DELETE", THEIRS)

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertIsNone(shared.deleted_at)

    def test_rename_of_a_read_only_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY
        )

        code, _, _ = self._request(
            "MOVE", THEIRS, headers={"Destination": f"{DAV}/{SHARED}/renamed.txt"}
        )

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertEqual(shared.name, "theirs.txt")

    def test_lock_of_a_read_only_file_is_refused(self):
        self._share(self._owned_file("theirs.txt"), FileShare.Permission.READ_ONLY)

        code, _, _ = self._request(
            "LOCK",
            THEIRS,
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

        code, _, body = self._request("GET", THEIRS)

        self.assertEqual(code, 200)
        self.assertEqual(body, b"theirs")

    # ── a file shared read-write ──

    def test_put_over_a_read_write_file_replaces_its_content(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )

        code, _, _ = self._request("PUT", THEIRS, body=b"edited")

        self.assertIn(code, (200, 204))
        self.assertEqual(self._body(shared), b"edited")

    def test_save_by_rename_over_a_read_write_file_replaces_its_content(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )
        self._request("PUT", f"{HOME}/.theirs.txt.tmp", body=b"edited")

        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/.theirs.txt.tmp",
            headers={"Destination": f"{DAV}{THEIRS}"},
        )

        self.assertLess(code, 300)
        self.assertEqual(self._body(shared), b"edited")

    def test_delete_of_a_read_write_file_is_refused(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )

        code, _, _ = self._request("DELETE", THEIRS)

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertIsNone(shared.deleted_at)

    def test_a_shared_file_cannot_be_moved_into_my_files(self):
        shared = self._share(
            self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE
        )

        code, _, _ = self._request(
            "MOVE", THEIRS, headers={"Destination": f"{DAV_HOME}/taken.txt"}
        )

        self.assertEqual(code, 403)
        shared.refresh_from_db()
        self.assertEqual(shared.owner, self.owner)
        self.assertIsNone(shared.parent_id)

    def test_copy_of_a_shared_file_is_refused_as_in_the_app(self):
        """Copying needs edit rights there too (``CopyAction``)."""
        self._share(self._owned_file("theirs.txt"), FileShare.Permission.READ_WRITE)

        code, _, _ = self._request(
            "COPY", THEIRS, headers={"Destination": f"{DAV_HOME}/copy.txt"}
        )

        self.assertEqual(code, 403)
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def _upload_to_a_file_purged_meanwhile(self, shared):
        environ = test_webdav._make_environ(self.user, CONTENT_LENGTH="6")
        resource = FileResource(THEIRS, environ, shared)
        resource.begin_write().write(b"edited")
        File.objects.filter(pk=shared.pk).delete()

        with self.assertRaises(DAVError) as raised:
            resource.end_write(with_errors=False)
        return raised.exception.value

    def test_an_upload_to_a_file_purged_meanwhile_creates_nothing_at_its_owners(self):
        """The share opened the file, never the folder it sat in."""
        folder = tree.create_folder(self.owner, "Private")
        shared = self._share(
            self._owned_file("theirs.txt", parent=folder),
            FileShare.Permission.READ_WRITE,
        )

        code = self._upload_to_a_file_purged_meanwhile(shared)

        self.assertEqual(code, HTTP_CONFLICT)
        self.assertFalse(File.objects.filter(parent=folder).exists())
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def test_an_upload_to_a_file_purged_meanwhile_creates_nothing_in_its_group(self):
        team = Group.objects.create(name="Team")
        self.owner.groups.add(team)
        root = tree.create_folder(self.owner, "Team", group=team)
        shared = self._share(
            self._owned_file("theirs.txt", parent=root),
            FileShare.Permission.READ_WRITE,
        )

        code = self._upload_to_a_file_purged_meanwhile(shared)

        self.assertEqual(code, HTTP_CONFLICT)
        self.assertFalse(File.objects.filter(parent=root).exists())
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def test_an_upload_to_an_own_file_purged_meanwhile_recreates_it(self):
        """A retry that discarded the row must not cost the upload its bytes."""
        folder = tree.create_folder(self.user, "Docs")
        mine = tree.create_file(self.user, "mine.txt", parent=folder)
        environ = test_webdav._make_environ(self.user, CONTENT_LENGTH="6")
        resource = FileResource(f"{HOME}/Docs/mine.txt", environ, mine)
        resource.begin_write().write(b"edited")
        File.objects.filter(pk=mine.pk).delete()

        resource.end_write(with_errors=False)

        self.assertEqual(self._body(File.objects.get(parent=folder)), b"edited")

    # ── a share row on a folder ──

    def test_a_folder_share_is_not_listed(self):
        self._folder_shared_by_hand()

        for folder in ("/", f"/{SHARED}", HOME):
            with self.subTest(folder=folder):
                code, _, body = self._request(
                    "PROPFIND", folder, headers={"Depth": "1"}
                )
                self.assertEqual(code, 207)
                self.assertNotIn(b"Theirs", body)

    def test_a_folder_share_opens_no_path(self):
        self._folder_shared_by_hand()

        for path in (
            f"/{SHARED}/Theirs",
            f"/{SHARED}/Theirs/doc.txt",
            f"{HOME}/Theirs",
            f"{HOME}/Theirs/doc.txt",
            "/Theirs/doc.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(self._request("GET", path)[0], 404)

    def test_nothing_is_written_into_a_folder_share(self):
        folder = self._folder_shared_by_hand()
        self._request("PUT", f"{HOME}/mine.txt", body=b"mine")
        inside = f"/{SHARED}/Theirs"

        self.assertEqual(self._request("PUT", f"{inside}/new.txt", body=b"n")[0], 409)
        self.assertEqual(self._request("MKCOL", f"{inside}/sub")[0], 409)
        for verb in ("COPY", "MOVE"):
            code, _, _ = self._request(
                verb,
                f"{HOME}/mine.txt",
                headers={"Destination": f"{DAV}{inside}/mine.txt"},
            )
            self.assertEqual(code, 409, verb)
        self.assertEqual(self._request("DELETE", inside)[0], 404)

        self.assertEqual(
            list(File.objects.filter(parent=folder).values_list("name", flat=True)),
            ["doc.txt"],
        )
        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)
        mine = File.objects.get(owner=self.user, name="mine.txt")
        self.assertIsNone(mine.parent_id)
        self.assertIsNone(mine.deleted_at)

    # ── the user's own files are unaffected ──

    def test_own_files_can_be_written_renamed_and_deleted(self):
        self.assertEqual(self._request("MKCOL", f"{HOME}/Mine")[0], 201)
        self.assertEqual(self._request("PUT", f"{HOME}/Mine/a.txt", body=b"a")[0], 201)
        self.assertEqual(self._request("PUT", f"{HOME}/Mine/a.txt", body=b"b")[0], 204)
        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/Mine/a.txt",
            headers={"Destination": f"{DAV_HOME}/Mine/b.txt"},
        )
        self.assertEqual(code, 201)
        self.assertEqual(self._request("DELETE", f"{HOME}/Mine/b.txt")[0], 204)


class WebDAVPathNamespaceTests(TestCase):
    """A DAV path is looked up in the tree its first folder names, never in
    another one by name.

    Every tree stores its own paths, so the user's "Docs/a.txt" and another
    user's "Docs/a.txt" carry the same ``File.path``. The other nodes are
    created first: a lookup by path alone would return them.
    """

    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        self._app = served_in_test(create_webdav_app())
        clear_auth_cache()
        self.user = User.objects.create_user(username="davguest", password="pass123")
        self.owner = User.objects.create_user(username="davowner", password="pass123")
        self.auth = test_webdav._basic_auth_header("davguest", "pass123")
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)
        self.owner.groups.add(self.team)

    def _file(self, user, name, body, parent=None, group=None):
        return tree.create_file(
            user,
            name,
            parent=parent,
            group=group,
            content=ContentFile(body, name=name),
            mime_type="text/plain",
        )

    def _body(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as f:
            return f.read()

    def _theirs_shared(self, permission=FileShare.Permission.READ_WRITE):
        docs = tree.create_folder(self.owner, "Docs")
        theirs = self._file(self.owner, "a.txt", b"theirs", parent=docs)
        FileShare.objects.create(
            file=theirs,
            shared_by=self.owner,
            shared_with=self.user,
            permission=permission,
        )
        return theirs

    def test_a_file_shared_inside_a_folder_is_not_reached_by_its_owner_path(self):
        self._theirs_shared()

        for path in (f"{HOME}/Docs/a.txt", "/Docs/a.txt", f"/{SHARED}/Docs/a.txt"):
            with self.subTest(path=path):
                self.assertEqual(self._request("GET", path)[0], 404)
        code, _, body = self._request("GET", f"/{SHARED}/a.txt")
        self.assertEqual((code, body), (200, b"theirs"))

    def test_the_user_own_file_is_not_the_shared_one_at_the_same_path(self):
        theirs = self._theirs_shared()
        docs = tree.create_folder(self.user, "Docs")
        mine = self._file(self.user, "a.txt", b"mine", parent=docs)

        code, _, body = self._request("GET", f"{HOME}/Docs/a.txt")
        self.assertEqual((code, body), (200, b"mine"))

        code, _, _ = self._request("PUT", f"{HOME}/Docs/a.txt", body=b"private")

        self.assertEqual(code, 204)
        self.assertEqual(self._body(mine), b"private")
        self.assertEqual(self._body(theirs), b"theirs")

    def test_a_copy_into_a_personal_folder_never_lands_in_a_group_folder(self):
        group_root = tree.create_folder(self.owner, "Team", group=self.team)
        mine = tree.create_folder(self.user, "Team")
        self._file(self.user, "private.txt", b"private")

        code, _, _ = self._request(
            "COPY",
            f"{HOME}/private.txt",
            headers={"Destination": f"{DAV_HOME}/Team/private.txt"},
        )

        self.assertEqual(code, 201)
        self.assertTrue(File.objects.filter(parent=mine, name="private.txt").exists())
        self.assertFalse(
            File.objects.filter(parent=group_root, name="private.txt").exists()
        )

    def test_a_file_shared_along_with_its_folder_is_served_with_the_shared_files(self):
        """The share on the folder opens no path; the one on the file does."""
        docs = tree.create_folder(self.owner, "Docs")
        theirs = self._file(self.owner, "a.txt", b"theirs", parent=docs)
        for node in (docs, theirs):
            FileShare.objects.create(
                file=node,
                shared_by=self.owner,
                shared_with=self.user,
                permission=FileShare.Permission.READ_ONLY,
            )

        code, _, body = self._request("GET", f"/{SHARED}/a.txt")

        self.assertEqual((code, body), (200, b"theirs"))
        self.assertEqual(self._request("GET", f"{HOME}/Docs/a.txt")[0], 404)
        self.assertEqual(self._request("GET", f"/{SHARED}/Docs/a.txt")[0], 404)
