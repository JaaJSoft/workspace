"""Tags published over WebDAV as Nextcloud's ``oc:system-tags`` property."""

from defusedxml import ElementTree as DefusedET
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.files.models import FileShare, FileTag, Tag
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.files.webdav.provider import WorkspaceDAVProvider
from workspace.files.webdav.resources import (
    SYSTEM_TAG_PROP,
    SYSTEM_TAGS_PROP,
    FileResource,
    FolderResource,
)

from .test_webdav import _basic_auth_header, _make_environ

User = get_user_model()


def _environ(user):
    """A WSGI environ carrying a wired provider - resources read both off it."""
    provider = WorkspaceDAVProvider()
    provider.set_share_path("/dav")
    return _make_environ(user=user, **{"wsgidav.provider": provider})


def _tag(user, file_obj, name):
    tag, _ = Tag.objects.get_or_create(owner=user, name=name)
    FileTag.objects.create(file=file_obj, tag=tag)
    return tag


def _tag_names(element):
    return [child.text for child in element]


class SystemTagsPropertyTests(TestCase):
    """The property itself, on a file and on a folder."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davtags", email="tags@test.com", password="pass"
        )
        self.environ = _environ(self.user)
        self.file = FileService.create_file(
            self.user, "invoice.pdf", mime_type="application/pdf"
        )
        self.folder = FileService.create_folder(self.user, "Accounting")

    def _file_resource(self, file_obj=None):
        file_obj = file_obj or self.file
        return FileResource(f"/{file_obj.name}", self.environ, file_obj)

    def _folder_resource(self):
        return FolderResource(f"/{self.folder.name}", self.environ, self.folder)

    def test_file_advertises_the_property(self):
        self.assertIn(
            SYSTEM_TAGS_PROP,
            self._file_resource().get_property_names(is_allprop=True),
        )

    def test_folder_advertises_the_property(self):
        self.assertIn(
            SYSTEM_TAGS_PROP,
            self._folder_resource().get_property_names(is_allprop=True),
        )

    def test_standard_properties_are_still_advertised(self):
        names = self._file_resource().get_property_names(is_allprop=True)
        self.assertIn("{DAV:}getetag", names)
        self.assertIn("{DAV:}getcontentlength", names)

    def test_file_publishes_its_tag_names(self):
        _tag(self.user, self.file, "Invoices")
        _tag(self.user, self.file, "2026")

        value = self._file_resource().get_property_value(SYSTEM_TAGS_PROP)

        self.assertEqual(value.tag, SYSTEM_TAGS_PROP)
        self.assertEqual([child.tag for child in value], [SYSTEM_TAG_PROP] * 2)
        self.assertEqual(_tag_names(value), ["2026", "Invoices"])

    def test_folder_publishes_its_tag_names(self):
        _tag(self.user, self.folder, "Archive")

        value = self._folder_resource().get_property_value(SYSTEM_TAGS_PROP)

        self.assertEqual(_tag_names(value), ["Archive"])

    def test_untagged_node_publishes_an_empty_property(self):
        value = self._file_resource().get_property_value(SYSTEM_TAGS_PROP)

        self.assertEqual(value.tag, SYSTEM_TAGS_PROP)
        self.assertEqual(len(value), 0)

    def test_other_properties_still_resolve(self):
        self.assertEqual(
            self._file_resource().get_property_value("{DAV:}getcontenttype"),
            "application/pdf",
        )

    def test_tags_of_the_sharer_are_not_published_to_the_recipient(self):
        other = User.objects.create_user(
            username="davtags2", email="tags2@test.com", password="pass"
        )
        shared = FileService.create_file(other, "budget.xlsx")
        _tag(other, shared, "Confidential")
        share_file(
            shared,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=other,
        )
        _tag(self.user, shared, "Received")

        value = self._file_resource(shared).get_property_value(SYSTEM_TAGS_PROP)

        self.assertEqual(_tag_names(value), ["Received"])


class SystemTagsListingQueryTests(TestCase):
    """A listing must not cost one query per member."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="davtagsq", email="tagsq@test.com", password="pass"
        )
        self.environ = _environ(self.user)

    def _listing_query_count(self, folder):
        resource = FolderResource(f"/{folder.name}", self.environ, folder)
        with CaptureQueriesContext(connection) as ctx:
            for member in resource.get_member_list():
                member.get_property_value(SYSTEM_TAGS_PROP)
        return len(ctx)

    def test_query_count_does_not_grow_with_the_number_of_members(self):
        small = FileService.create_folder(self.user, "Small")
        _tag(self.user, FileService.create_file(self.user, "a.txt", parent=small), "T")

        big = FileService.create_folder(self.user, "Big")
        for index in range(5):
            child = FileService.create_file(self.user, f"f{index}.txt", parent=big)
            _tag(self.user, child, "T")

        self.assertEqual(
            self._listing_query_count(big),
            self._listing_query_count(small),
        )


class SystemTagsPropfindTests(TestCase):
    """End-to-end, through the WsgiDAV app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from workspace.files.webdav.app import create_webdav_app

        cls._app = create_webdav_app()

    def setUp(self):
        self.user = User.objects.create_user(
            username="davtagsprop", email="tagsprop@test.com", password="pass123"
        )
        self.auth = _basic_auth_header("davtagsprop", "pass123")
        self.file = FileService.create_file(self.user, "report.txt")
        _tag(self.user, self.file, "Invoices")

    def _request(self, method, path, body=b"", headers=None):
        import io

        env = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "/dav",
            "PATH_INFO": path,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "testserver",
            "HTTP_AUTHORIZATION": self.auth,
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.BytesIO(),
            "wsgi.url_scheme": "http",
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/xml",
        }
        for key, value in (headers or {}).items():
            env["HTTP_" + key.upper().replace("-", "_")] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status

        result = self._app(env, start_response)
        out = b"".join(result)
        if hasattr(result, "close"):
            result.close()
        return int(captured["status"].split(" ", 1)[0]), out

    def test_propfind_returns_the_tag_names(self):
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b"<d:prop><oc:system-tags/></d:prop></d:propfind>"
        )

        code, out = self._request(
            "PROPFIND", "/report.txt", body=body, headers={"Depth": "0"}
        )

        self.assertEqual(code, 207)
        root = DefusedET.fromstring(out.decode())
        self.assertEqual(
            [el.text for el in root.iter(SYSTEM_TAG_PROP)],
            ["Invoices"],
        )

    def test_proppatch_on_the_property_is_refused(self):
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propertyupdate xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            b"<d:set><d:prop><oc:system-tags><oc:system-tag>New</oc:system-tag>"
            b"</oc:system-tags></d:prop></d:set></d:propertyupdate>"
        )

        code, out = self._request("PROPPATCH", "/report.txt", body=body)

        self.assertIn(code, (207, 403))
        if code == 207:
            self.assertIn("403", out.decode())
        self.assertFalse(Tag.objects.filter(name="New").exists())
