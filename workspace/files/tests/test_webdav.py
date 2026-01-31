"""Tests for the WebDAV integration (domain controller, provider, resources)."""

import base64
import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.webdav.dc import DjangoBasicDomainController
from workspace.files.webdav.provider import WorkspaceDAVProvider
from workspace.files.webdav.resources import (
    FileResource,
    FolderResource,
    RootCollection,
    _WriteBuffer,
    _copy_as,
    _resolve_parent,
)

User = get_user_model()


def _make_environ(user=None, **extra):
    """Build a minimal WSGI environ dict for resource/provider tests."""
    env = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "HTTP_HOST": "testserver",
        "wsgi.input": io.BytesIO(b""),
        "wsgidav.provider": None,
    }
    if user is not None:
        env["workspace.user"] = user
    env.update(extra)
    return env


# ── Domain Controller ─────────────────────────────────────────────────


class DomainControllerTests(TestCase):
    """Tests for DjangoBasicDomainController."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davdc", email="dc@test.com", password="secret123"
        )
        # DC requires a wsgidav_app + config; we pass minimal stubs.
        self.dc = DjangoBasicDomainController(None, {})

    def test_get_domain_realm(self):
        self.assertEqual(self.dc.get_domain_realm("/", {}), "Workspace")

    def test_require_authentication(self):
        self.assertTrue(self.dc.require_authentication("Workspace", {}))

    def test_digest_auth_not_supported(self):
        self.assertFalse(self.dc.supports_http_digest_auth())

    def test_basic_auth_valid_credentials(self):
        environ = {}
        result = self.dc.basic_auth_user("Workspace", "davdc", "secret123", environ)
        self.assertTrue(result)
        self.assertEqual(environ["workspace.user"], self.user)

    def test_basic_auth_wrong_password(self):
        environ = {}
        result = self.dc.basic_auth_user("Workspace", "davdc", "wrong", environ)
        self.assertFalse(result)
        self.assertNotIn("workspace.user", environ)

    def test_basic_auth_unknown_user(self):
        environ = {}
        result = self.dc.basic_auth_user("Workspace", "nobody", "pass", environ)
        self.assertFalse(result)

    def test_basic_auth_inactive_user(self):
        self.user.is_active = False
        self.user.save()
        environ = {}
        result = self.dc.basic_auth_user("Workspace", "davdc", "secret123", environ)
        self.assertFalse(result)


# ── Provider ──────────────────────────────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class ProviderTests(TestCase):
    """Tests for WorkspaceDAVProvider."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davprov", email="prov@test.com", password="pass"
        )
        self.provider = WorkspaceDAVProvider()
        self.environ = _make_environ(user=self.user)

    def test_root_returns_root_collection(self):
        res = self.provider.get_resource_inst("/", self.environ)
        self.assertIsInstance(res, RootCollection)

    def test_nonexistent_path_returns_none(self):
        res = self.provider.get_resource_inst("/no/such/path", self.environ)
        self.assertIsNone(res)

    def test_file_path_returns_file_resource(self):
        FileService.create_file(self.user, "doc.txt", mime_type="text/plain")
        res = self.provider.get_resource_inst("/doc.txt", self.environ)
        self.assertIsInstance(res, FileResource)

    def test_folder_path_returns_folder_resource(self):
        FileService.create_folder(self.user, "Photos")
        res = self.provider.get_resource_inst("/Photos", self.environ)
        self.assertIsInstance(res, FolderResource)

    def test_nested_path_resolved(self):
        folder = FileService.create_folder(self.user, "A")
        FileService.create_file(self.user, "b.txt", parent=folder, mime_type="text/plain")
        res = self.provider.get_resource_inst("/A/b.txt", self.environ)
        self.assertIsInstance(res, FileResource)

    def test_soft_deleted_file_not_resolved(self):
        f = FileService.create_file(self.user, "gone.txt", mime_type="text/plain")
        f.soft_delete()
        res = self.provider.get_resource_inst("/gone.txt", self.environ)
        self.assertIsNone(res)

    def test_no_user_returns_none(self):
        env = _make_environ()  # no user
        res = self.provider.get_resource_inst("/", env)
        self.assertIsNone(res)

    def test_trailing_slash_stripped(self):
        FileService.create_folder(self.user, "Stuff")
        res = self.provider.get_resource_inst("/Stuff/", self.environ)
        self.assertIsInstance(res, FolderResource)

    def test_user_isolation(self):
        """Files from another user are not visible."""
        other = User.objects.create_user(
            username="other", email="o@test.com", password="p"
        )
        FileService.create_file(other, "secret.txt", mime_type="text/plain")
        res = self.provider.get_resource_inst("/secret.txt", self.environ)
        self.assertIsNone(res)


# ── RootCollection ────────────────────────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class RootCollectionTests(TestCase):
    """Tests for the virtual root resource."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davroot", email="root@test.com", password="pass"
        )
        self.environ = _make_environ(user=self.user)
        self.root = RootCollection("/", self.environ)

    def test_member_names_empty(self):
        self.assertEqual(self.root.get_member_names(), [])

    def test_member_names_lists_root_items(self):
        FileService.create_folder(self.user, "A")
        FileService.create_file(self.user, "b.txt", mime_type="text/plain")
        names = self.root.get_member_names()
        self.assertIn("A", names)
        self.assertIn("b.txt", names)
        self.assertEqual(len(names), 2)

    def test_member_names_excludes_deleted(self):
        f = FileService.create_file(self.user, "gone.txt", mime_type="text/plain")
        f.soft_delete()
        self.assertEqual(self.root.get_member_names(), [])

    def test_member_names_excludes_nested(self):
        folder = FileService.create_folder(self.user, "Dir")
        FileService.create_file(self.user, "child.txt", parent=folder, mime_type="text/plain")
        names = self.root.get_member_names()
        self.assertEqual(names, ["Dir"])

    def test_get_member_folder(self):
        FileService.create_folder(self.user, "Docs")
        member = self.root.get_member("Docs")
        self.assertIsInstance(member, FolderResource)

    def test_get_member_file(self):
        FileService.create_file(self.user, "a.txt", mime_type="text/plain")
        member = self.root.get_member("a.txt")
        self.assertIsInstance(member, FileResource)

    def test_get_member_missing(self):
        self.assertIsNone(self.root.get_member("nope"))

    def test_create_empty_resource(self):
        res = self.root.create_empty_resource("new.txt")
        self.assertIsInstance(res, FileResource)
        self.assertTrue(File.objects.filter(owner=self.user, name="new.txt").exists())

    def test_create_collection(self):
        self.root.create_collection("NewDir")
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="NewDir", node_type=File.NodeType.FOLDER
            ).exists()
        )


# ── FolderResource ────────────────────────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class FolderResourceTests(TestCase):
    """Tests for FolderResource."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davfold", email="fold@test.com", password="pass"
        )
        self.environ = _make_environ(user=self.user)
        self.folder = FileService.create_folder(self.user, "Docs")
        self.res = FolderResource("/Docs", self.environ, self.folder)

    def test_display_info(self):
        self.assertEqual(self.res.get_display_info(), {"type": "Directory"})

    def test_timestamps(self):
        self.assertEqual(
            self.res.get_creation_date(), self.folder.created_at.timestamp()
        )
        self.assertEqual(
            self.res.get_last_modified(), self.folder.updated_at.timestamp()
        )

    def test_member_names(self):
        FileService.create_file(self.user, "a.txt", parent=self.folder, mime_type="text/plain")
        FileService.create_folder(self.user, "Sub", parent=self.folder)
        names = self.res.get_member_names()
        self.assertCountEqual(names, ["a.txt", "Sub"])

    def test_member_names_excludes_deleted(self):
        f = FileService.create_file(
            self.user, "del.txt", parent=self.folder, mime_type="text/plain"
        )
        f.soft_delete()
        self.assertEqual(self.res.get_member_names(), [])

    def test_get_member_returns_correct_type(self):
        FileService.create_file(self.user, "f.txt", parent=self.folder, mime_type="text/plain")
        FileService.create_folder(self.user, "D", parent=self.folder)
        self.assertIsInstance(self.res.get_member("f.txt"), FileResource)
        self.assertIsInstance(self.res.get_member("D"), FolderResource)
        self.assertIsNone(self.res.get_member("nope"))

    def test_create_empty_resource(self):
        res = self.res.create_empty_resource("new.txt")
        self.assertIsInstance(res, FileResource)
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="new.txt", parent=self.folder
            ).exists()
        )

    def test_create_collection(self):
        self.res.create_collection("Sub")
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="Sub", parent=self.folder,
                node_type=File.NodeType.FOLDER,
            ).exists()
        )

    def test_delete_soft_deletes(self):
        self.res.delete()
        self.folder.refresh_from_db()
        self.assertIsNotNone(self.folder.deleted_at)

    def test_support_recursive_move(self):
        self.assertTrue(self.res.support_recursive_move("/somewhere"))

    def test_support_recursive_delete(self):
        self.assertTrue(self.res.support_recursive_delete())

    def test_move_recursive(self):
        target = FileService.create_folder(self.user, "Target")
        self.res.move_recursive("/Target/Renamed")
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "Renamed")
        self.assertEqual(self.folder.parent, target)

    def test_copy_move_single_copies(self):
        """copy_move_single always copies (used by WsgiDAV's copy flow)."""
        target = FileService.create_folder(self.user, "Dest")
        self.res.copy_move_single("/Dest/DocsCopy", is_move=False)
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="DocsCopy", parent=target,
                node_type=File.NodeType.FOLDER, deleted_at__isnull=True,
            ).exists()
        )
        # Original still exists
        self.folder.refresh_from_db()
        self.assertIsNone(self.folder.deleted_at)


# ── FileResource ──────────────────────────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class FileResourceTests(TestCase):
    """Tests for FileResource."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davfile", email="file@test.com", password="pass"
        )
        self.environ = _make_environ(user=self.user)
        content = ContentFile(b"hello world", name="test.txt")
        self.file = FileService.create_file(
            self.user, "test.txt", content=content, mime_type="text/plain"
        )
        self.res = FileResource("/test.txt", self.environ, self.file)

    def test_content_length(self):
        self.assertEqual(self.res.get_content_length(), 11)

    def test_content_type(self):
        self.assertEqual(self.res.get_content_type(), "text/plain")

    def test_timestamps(self):
        self.assertEqual(
            self.res.get_creation_date(), self.file.created_at.timestamp()
        )
        self.assertEqual(
            self.res.get_last_modified(), self.file.updated_at.timestamp()
        )

    def test_get_content(self):
        stream = self.res.get_content()
        data = stream.read()
        stream.close()
        self.assertEqual(data, b"hello world")

    def test_get_content_empty_file(self):
        empty = FileService.create_file(self.user, "empty.txt", mime_type="text/plain")
        res = FileResource("/empty.txt", self.environ, empty)
        stream = res.get_content()
        self.assertEqual(stream.read(), b"")

    def test_begin_end_write(self):
        buf = self.res.begin_write(content_type="text/plain")
        buf.write(b"new content")
        buf.close()  # WsgiDAV closes before end_write
        self.res.end_write(with_errors=False)

        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 11)
        self.file.content.open("rb")
        self.assertEqual(self.file.content.read(), b"new content")
        self.file.content.close()

    def test_end_write_with_errors_on_new_file(self):
        new = FileService.create_file(self.user, "fail.txt", mime_type="text/plain")
        res = FileResource("/fail.txt", self.environ, new)
        buf = res.begin_write()
        buf.write(b"data")
        buf.close()
        res.end_write(with_errors=True)
        # New file (size=None) should be hard-deleted
        self.assertFalse(File.objects.filter(pk=new.pk).exists())

    def test_end_write_with_errors_on_existing_file(self):
        """Existing file (size != None) is kept on write error."""
        self.res.begin_write()
        self.res.end_write(with_errors=True)
        self.file.refresh_from_db()
        self.assertTrue(File.objects.filter(pk=self.file.pk).exists())

    def test_delete_soft_deletes(self):
        self.res.delete()
        self.file.refresh_from_db()
        self.assertIsNotNone(self.file.deleted_at)

    def test_delete_noop_after_move(self):
        """After copy_move_single(is_move=True), delete() is a no-op."""
        folder = FileService.create_folder(self.user, "Dest")
        self.res.copy_move_single("/Dest/test.txt", is_move=True)
        self.res.delete()  # should be a no-op
        self.file.refresh_from_db()
        self.assertIsNone(self.file.deleted_at)

    def test_copy_move_single_move(self):
        folder = FileService.create_folder(self.user, "Target")
        self.res.copy_move_single("/Target/moved.txt", is_move=True)
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, "moved.txt")
        self.assertEqual(self.file.parent, folder)

    def test_copy_move_single_copy(self):
        folder = FileService.create_folder(self.user, "CopyDest")
        self.res.copy_move_single("/CopyDest/copy.txt", is_move=False)
        copy = File.objects.get(
            owner=self.user, name="copy.txt", parent=folder,
            deleted_at__isnull=True,
        )
        self.assertEqual(copy.node_type, File.NodeType.FILE)
        # Original unchanged
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, "test.txt")

    def test_support_etag(self):
        self.assertTrue(self.res.support_etag())

    def test_get_etag(self):
        etag = self.res.get_etag()
        self.assertIn(str(self.file.uuid), etag)

    def test_support_recursive_move_false(self):
        self.assertFalse(self.res.support_recursive_move("/x"))

    def test_support_content_length(self):
        self.assertTrue(self.res.support_content_length())


# ── Helpers ───────────────────────────────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class ResolveParentTests(TestCase):
    """Tests for _resolve_parent helper."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davrp", email="rp@test.com", password="pass"
        )

    def test_empty_parts_returns_none(self):
        self.assertIsNone(_resolve_parent(self.user, []))

    def test_single_folder(self):
        folder = FileService.create_folder(self.user, "A")
        self.assertEqual(_resolve_parent(self.user, ["A"]), folder)

    def test_nested_folders(self):
        a = FileService.create_folder(self.user, "A")
        b = FileService.create_folder(self.user, "B", parent=a)
        self.assertEqual(_resolve_parent(self.user, ["A", "B"]), b)

    def test_nonexistent_returns_none(self):
        self.assertIsNone(_resolve_parent(self.user, ["X"]))

    def test_file_not_treated_as_folder(self):
        FileService.create_file(self.user, "f.txt", mime_type="text/plain")
        self.assertIsNone(_resolve_parent(self.user, ["f.txt"]))

    def test_deleted_folder_not_resolved(self):
        folder = FileService.create_folder(self.user, "Del")
        folder.soft_delete()
        self.assertIsNone(_resolve_parent(self.user, ["Del"]))


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class CopyAsTests(TestCase):
    """Tests for _copy_as helper."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davcp", email="cp@test.com", password="pass"
        )

    def test_copy_file_with_exact_name(self):
        content = ContentFile(b"data", name="orig.txt")
        orig = FileService.create_file(
            self.user, "orig.txt", content=content, mime_type="text/plain"
        )
        copy = _copy_as(orig, None, self.user, "renamed.txt")
        self.assertEqual(copy.name, "renamed.txt")
        self.assertNotEqual(copy.pk, orig.pk)
        self.assertEqual(copy.mime_type, "text/plain")

    def test_copy_file_without_content(self):
        orig = FileService.create_file(self.user, "empty.txt", mime_type="text/plain")
        copy = _copy_as(orig, None, self.user, "empty_copy.txt")
        self.assertEqual(copy.name, "empty_copy.txt")

    def test_copy_folder_recursive(self):
        folder = FileService.create_folder(self.user, "Src")
        content = ContentFile(b"child", name="c.txt")
        FileService.create_file(
            self.user, "c.txt", parent=folder, content=content, mime_type="text/plain"
        )
        sub = FileService.create_folder(self.user, "Sub", parent=folder)
        FileService.create_file(
            self.user, "deep.txt", parent=sub,
            content=ContentFile(b"deep", name="deep.txt"), mime_type="text/plain",
        )

        copy = _copy_as(folder, None, self.user, "Dst")
        self.assertEqual(copy.name, "Dst")
        children = File.objects.filter(parent=copy, deleted_at__isnull=True)
        self.assertEqual(children.count(), 2)
        child_names = set(children.values_list("name", flat=True))
        self.assertIn("c.txt", child_names)
        self.assertIn("Sub", child_names)
        # Check grandchild
        sub_copy = children.get(node_type=File.NodeType.FOLDER)
        grandchildren = File.objects.filter(parent=sub_copy, deleted_at__isnull=True)
        self.assertEqual(grandchildren.count(), 1)
        self.assertEqual(grandchildren.first().name, "deep.txt")

    def test_copy_into_folder(self):
        target = FileService.create_folder(self.user, "Target")
        content = ContentFile(b"x", name="x.txt")
        orig = FileService.create_file(
            self.user, "x.txt", content=content, mime_type="text/plain"
        )
        copy = _copy_as(orig, target, self.user, "y.txt")
        self.assertEqual(copy.parent, target)
        self.assertEqual(copy.name, "y.txt")


class WriteBufferTests(TestCase):
    """Tests for _WriteBuffer deferred-close wrapper."""

    def test_write_and_read(self):
        buf = _WriteBuffer()
        buf.write(b"hello ")
        buf.write(b"world")
        buf.close()  # no-op
        data = buf.read_all_and_close()
        self.assertEqual(data, b"hello world")

    def test_writelines(self):
        buf = _WriteBuffer()
        buf.writelines([b"a", b"b", b"c"])
        data = buf.read_all_and_close()
        self.assertEqual(data, b"abc")

    def test_real_close(self):
        buf = _WriteBuffer()
        buf.write(b"data")
        buf.real_close()
        with self.assertRaises(ValueError):
            buf.read_all_and_close()


# ── Integration (full WSGI stack) ─────────────────────────────────────


def _basic_auth_header(username, password):
    cred = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {cred}"


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class WebDAVIntegrationTests(TestCase):
    """End-to-end tests hitting the WsgiDAV app through the WSGI dispatch."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from workspace.files.webdav.app import create_webdav_app

        cls._app = create_webdav_app()

    def setUp(self):
        self.user = User.objects.create_user(
            username="davint", email="int@test.com", password="pass123"
        )
        self.auth = _basic_auth_header("davint", "pass123")

    # ── helpers ──

    def _request(self, method, path, body=b"", headers=None):
        """Send a raw WSGI request to the WebDAV app and return (status, headers, body)."""
        env = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "/dav",
            "PATH_INFO": path,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "testserver",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.BytesIO(),
            "wsgi.url_scheme": "http",
            "CONTENT_LENGTH": str(len(body)),
        }
        if headers:
            for key, value in headers.items():
                wsgi_key = "HTTP_" + key.upper().replace("-", "_")
                env[wsgi_key] = value
        # Authorization goes into HTTP_AUTHORIZATION
        if "HTTP_AUTHORIZATION" not in env and self.auth:
            env["HTTP_AUTHORIZATION"] = self.auth
        # CONTENT_TYPE from headers
        if headers and "Content-Type" in headers:
            env["CONTENT_TYPE"] = headers["Content-Type"]

        captured = {"status": None, "headers": []}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = response_headers

        result = self._app(env, start_response)
        body_out = b"".join(result)
        if hasattr(result, "close"):
            result.close()

        status_code = int(captured["status"].split(" ", 1)[0])
        return status_code, dict(captured["headers"]), body_out

    # ── auth ──

    def test_unauthenticated_returns_401(self):
        self.auth = None
        code, _, _ = self._request("PROPFIND", "/", headers={"Depth": "0"})
        self.assertEqual(code, 401)

    def test_wrong_password_returns_401(self):
        self.auth = _basic_auth_header("davint", "wrong")
        code, _, _ = self._request("PROPFIND", "/", headers={"Depth": "0"})
        self.assertEqual(code, 401)

    # ── PROPFIND ──

    def test_propfind_root_empty(self):
        code, _, body = self._request("PROPFIND", "/", headers={"Depth": "0"})
        self.assertEqual(code, 207)

    def test_propfind_root_lists_members(self):
        FileService.create_folder(self.user, "F1")
        FileService.create_file(self.user, "a.txt", mime_type="text/plain")
        code, _, body = self._request("PROPFIND", "/", headers={"Depth": "1"})
        self.assertEqual(code, 207)
        body_str = body.decode("utf-8", errors="replace")
        self.assertIn("F1", body_str)
        self.assertIn("a.txt", body_str)

    def test_propfind_excludes_deleted(self):
        f = FileService.create_file(self.user, "gone.txt", mime_type="text/plain")
        f.soft_delete()
        code, _, body = self._request("PROPFIND", "/", headers={"Depth": "1"})
        self.assertEqual(code, 207)
        self.assertNotIn(b"gone.txt", body)

    # ── MKCOL ──

    def test_mkcol_creates_folder(self):
        code, _, _ = self._request("MKCOL", "/NewFolder/")
        self.assertEqual(code, 201)
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="NewFolder", node_type=File.NodeType.FOLDER,
            ).exists()
        )

    def test_mkcol_nested(self):
        FileService.create_folder(self.user, "Parent")
        code, _, _ = self._request("MKCOL", "/Parent/Child/")
        self.assertEqual(code, 201)
        parent = File.objects.get(owner=self.user, name="Parent")
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="Child", parent=parent,
            ).exists()
        )

    # ── PUT / GET ──

    def test_put_creates_file(self):
        code, _, _ = self._request(
            "PUT", "/hello.txt", body=b"Hello!",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(code, 201)
        f = File.objects.get(owner=self.user, name="hello.txt")
        self.assertEqual(f.size, 6)

    def test_get_returns_content(self):
        content = ContentFile(b"file data here", name="data.txt")
        FileService.create_file(
            self.user, "data.txt", content=content, mime_type="text/plain"
        )
        code, hdrs, body = self._request("GET", "/data.txt")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"file data here")

    def test_put_overwrite(self):
        content = ContentFile(b"old", name="ow.txt")
        FileService.create_file(
            self.user, "ow.txt", content=content, mime_type="text/plain"
        )
        code, _, _ = self._request("PUT", "/ow.txt", body=b"new content")
        self.assertEqual(code, 204)
        f = File.objects.get(owner=self.user, name="ow.txt", deleted_at__isnull=True)
        self.assertEqual(f.size, 11)

    def test_put_in_subfolder(self):
        FileService.create_folder(self.user, "Sub")
        code, _, _ = self._request("PUT", "/Sub/note.txt", body=b"note")
        self.assertEqual(code, 201)
        parent = File.objects.get(owner=self.user, name="Sub")
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="note.txt", parent=parent,
            ).exists()
        )

    # ── DELETE ──

    def test_delete_file(self):
        FileService.create_file(self.user, "del.txt", mime_type="text/plain")
        code, _, _ = self._request("DELETE", "/del.txt")
        self.assertEqual(code, 204)
        f = File.objects.get(owner=self.user, name="del.txt")
        self.assertIsNotNone(f.deleted_at)

    def test_delete_folder(self):
        folder = FileService.create_folder(self.user, "RmDir")
        FileService.create_file(
            self.user, "inside.txt", parent=folder, mime_type="text/plain"
        )
        code, _, _ = self._request("DELETE", "/RmDir/")
        self.assertEqual(code, 204)
        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)

    def test_delete_nonexistent_returns_404(self):
        code, _, _ = self._request("DELETE", "/nope.txt")
        self.assertEqual(code, 404)

    # ── MOVE ──

    def test_move_file(self):
        content = ContentFile(b"move me", name="src.txt")
        FileService.create_file(
            self.user, "src.txt", content=content, mime_type="text/plain"
        )
        FileService.create_folder(self.user, "Dest")

        code, _, _ = self._request(
            "MOVE", "/src.txt",
            headers={"Destination": "http://testserver/dav/Dest/src.txt"},
        )
        self.assertIn(code, (201, 204))

        # Old location gone
        self.assertFalse(
            File.objects.filter(
                owner=self.user, name="src.txt", parent__isnull=True,
                deleted_at__isnull=True,
            ).exists()
        )
        # New location exists
        dest = File.objects.get(owner=self.user, name="Dest")
        moved = File.objects.get(
            owner=self.user, name="src.txt", parent=dest, deleted_at__isnull=True,
        )
        self.assertEqual(moved.size, 7)

    def test_move_folder(self):
        folder = FileService.create_folder(self.user, "ToMove")
        FileService.create_file(
            self.user, "child.txt", parent=folder,
            content=ContentFile(b"c", name="child.txt"),
        )
        FileService.create_folder(self.user, "Into")

        code, _, _ = self._request(
            "MOVE", "/ToMove/",
            headers={"Destination": "http://testserver/dav/Into/ToMove/"},
        )
        self.assertIn(code, (201, 204))
        into = File.objects.get(owner=self.user, name="Into")
        moved = File.objects.get(
            owner=self.user, name="ToMove", parent=into, deleted_at__isnull=True,
        )
        # Child should follow
        self.assertTrue(
            File.objects.filter(parent=moved, name="child.txt", deleted_at__isnull=True).exists()
        )

    # ── COPY ──

    def test_copy_file(self):
        content = ContentFile(b"copy me", name="orig.txt")
        FileService.create_file(
            self.user, "orig.txt", content=content, mime_type="text/plain"
        )
        code, _, _ = self._request(
            "COPY", "/orig.txt",
            headers={"Destination": "http://testserver/dav/dup.txt"},
        )
        self.assertIn(code, (201, 204))
        # Both should exist
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="orig.txt", deleted_at__isnull=True,
            ).exists()
        )
        dup = File.objects.get(
            owner=self.user, name="dup.txt", deleted_at__isnull=True,
        )
        dup.content.open("rb")
        self.assertEqual(dup.content.read(), b"copy me")
        dup.content.close()

    # ── GET nonexistent ──

    def test_get_nonexistent_returns_404(self):
        code, _, _ = self._request("GET", "/nope.txt")
        self.assertEqual(code, 404)

    # ── user isolation ──

    def test_cannot_see_other_users_files(self):
        other = User.objects.create_user(
            username="other2", email="o2@test.com", password="p"
        )
        FileService.create_file(other, "secret.txt", mime_type="text/plain")
        code, _, body = self._request("PROPFIND", "/", headers={"Depth": "1"})
        self.assertEqual(code, 207)
        self.assertNotIn(b"secret.txt", body)
