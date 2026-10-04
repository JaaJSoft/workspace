"""Names reach the app in either Unicode form and are stored composed (NFC).

macOS writes "café" decomposed - an "e" followed by a combining accent - where
Linux and Windows write one precomposed character. Both look the same, and
both must be the same node with the same storage key.
"""

import io
import unicodedata

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService
from workspace.files.webdav.provider import WorkspaceDAVProvider
from workspace.files.webdav.resources import FolderResource

User = get_user_model()

COMPOSED = unicodedata.normalize("NFC", "café.txt")
DECOMPOSED = unicodedata.normalize("NFD", "café.txt")
FOLDER_COMPOSED = unicodedata.normalize("NFC", "Été")
FOLDER_DECOMPOSED = unicodedata.normalize("NFD", "Été")


def _environ(user):
    return {
        "REQUEST_METHOD": "PUT",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/",
        "wsgi.input": io.BytesIO(b""),
        "wsgidav.provider": None,
        "workspace.user": user,
    }


class NameFormsTestCase(IsolatedMediaRootMixin, TestCase):
    """Each test lists the blobs of a fresh media root."""

    def setUp(self):
        super().setUp()
        self.assertNotEqual(COMPOSED, DECOMPOSED)
        self.user = User.objects.create_user(username="nfc", password="pass")
        self.storage = storages["files"]

    def blobs(self, directory):
        return sorted(entry.name for entry in self.storage.scan(directory))


class ServiceNameTests(NameFormsTestCase):
    def test_a_decomposed_name_is_stored_composed(self):
        f = FileService.create_file(
            self.user, DECOMPOSED, content=ContentFile(b"x", name=DECOMPOSED)
        )

        self.assertEqual(f.name, COMPOSED)
        self.assertEqual(f.content.name, f"files/users/nfc/{COMPOSED}")
        self.assertEqual(self.blobs("files/users/nfc"), [COMPOSED])

    def test_a_decomposed_folder_name_is_stored_composed(self):
        folder = FileService.create_folder(self.user, FOLDER_DECOMPOSED)

        self.assertEqual(folder.name, FOLDER_COMPOSED)
        self.assertEqual(self.blobs("files/users/nfc"), [FOLDER_COMPOSED])

    def test_the_two_forms_of_a_name_are_one_name(self):
        FileService.create_file(self.user, COMPOSED)

        with self.assertRaises(ValueError):
            FileService.create_file(self.user, DECOMPOSED)

    def test_renaming_to_the_other_form_changes_nothing(self):
        f = FileService.create_file(
            self.user, COMPOSED, content=ContentFile(b"x", name=COMPOSED)
        )

        FileService.rename(f, DECOMPOSED)

        f.refresh_from_db()
        self.assertEqual(f.name, COMPOSED)
        self.assertEqual(self.blobs("files/users/nfc"), [COMPOSED])

    def test_a_rename_stores_the_new_name_composed(self):
        f = FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"x", name="a.txt")
        )

        FileService.rename(f, DECOMPOSED)

        f.refresh_from_db()
        self.assertEqual(f.name, COMPOSED)
        self.assertEqual(f.content.name, f"files/users/nfc/{COMPOSED}")
        self.assertEqual(self.blobs("files/users/nfc"), [COMPOSED])

    def test_a_copy_of_a_decomposed_name_is_composed(self):
        """A row named before names were composed copies into the new form."""
        source = FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"x", name="a.txt")
        )
        decomposed_key = f"files/users/nfc/{DECOMPOSED}"
        self.storage.move(source.content.name, decomposed_key)
        File.objects.filter(pk=source.pk).update(
            name=DECOMPOSED, path=DECOMPOSED, content=decomposed_key
        )
        source.refresh_from_db()
        target = FileService.create_folder(self.user, "Copies")

        copied = FileService.copy(source, target, self.user)

        self.assertEqual(copied.name, COMPOSED)
        self.assertEqual(copied.content.name, f"files/users/nfc/Copies/{COMPOSED}")

    def test_replacing_content_keeps_the_blob_at_the_tree_path(self):
        """Whatever the upload is called: a "REPORT.TXT" written over
        "report.txt" (the names match case-insensitively) or the same name
        sent decomposed."""
        f = FileService.create_file(
            self.user, "report.txt", content=ContentFile(b"old", name="report.txt")
        )

        FileService.update_content(f, ContentFile(b"new", name="REPORT.TXT"))

        f.refresh_from_db()
        self.assertEqual(f.content.name, "files/users/nfc/report.txt")
        self.assertEqual(self.blobs("files/users/nfc"), ["report.txt"])
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"new")


class ApiNameTests(NameFormsTestCase):
    def setUp(self):
        super().setUp()
        self.existing = FileService.create_file(
            self.user, COMPOSED, content=ContentFile(b"old", name=COMPOSED)
        )
        self.client.force_login(self.user)

    def upload(self, **extra):
        return self.client.post(
            "/api/v1/files",
            {
                "name": DECOMPOSED,
                "node_type": File.NodeType.FILE,
                "content": SimpleUploadedFile(DECOMPOSED, b"new"),
                **extra,
            },
            format="multipart",
        )

    def test_an_upload_named_decomposed_meets_the_composed_file(self):
        response = self.upload()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(File.objects.filter(owner=self.user).count(), 1)

    def test_an_upload_named_decomposed_replaces_the_composed_file(self):
        response = self.upload(on_conflict="replace")

        self.assertEqual(response.status_code, 200)
        self.existing.refresh_from_db()
        with self.existing.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"new")
        self.assertEqual(self.blobs("files/users/nfc"), [COMPOSED])


class SyncNameTests(NameFormsTestCase):
    def sync(self):
        return FileSyncService().sync_user_recursive(self.user)

    def test_a_decomposed_entry_is_adopted_under_its_composed_name(self):
        self.storage.save(f"files/users/nfc/{DECOMPOSED}", ContentFile(b"from a mac"))

        result = self.sync()

        self.assertEqual(result.files_created, 1)
        f = File.objects.get(owner=self.user)
        self.assertEqual(f.name, COMPOSED)
        self.assertEqual(f.content.name, f"files/users/nfc/{COMPOSED}")
        self.assertEqual(self.blobs("files/users/nfc"), [COMPOSED])
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"from a mac")

    def test_a_decomposed_folder_is_adopted_with_what_it_holds(self):
        self.storage.save(
            f"files/users/nfc/{FOLDER_DECOMPOSED}/a.txt", ContentFile(b"inside")
        )

        self.sync()

        folder = File.objects.get(owner=self.user, node_type=File.NodeType.FOLDER)
        child = File.objects.get(owner=self.user, node_type=File.NodeType.FILE)
        self.assertEqual(folder.name, FOLDER_COMPOSED)
        self.assertEqual(child.parent, folder)
        self.assertEqual(child.content.name, f"files/users/nfc/{FOLDER_COMPOSED}/a.txt")

    def test_a_row_named_decomposed_still_matches_its_entry(self):
        f = FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"x", name="a.txt")
        )
        decomposed_key = f"files/users/nfc/{DECOMPOSED}"
        self.storage.move(f.content.name, decomposed_key)
        File.objects.filter(pk=f.pk).update(
            name=DECOMPOSED, path=DECOMPOSED, content=decomposed_key
        )

        result = self.sync()

        self.assertEqual((result.files_created, result.files_soft_deleted), (0, 0))
        self.assertTrue(File.objects.filter(pk=f.pk, deleted_at__isnull=True).exists())

    def test_two_entries_one_name_apart_in_form_adopt_the_composed_one(self):
        self.storage.save(f"files/users/nfc/{COMPOSED}", ContentFile(b"composed"))
        self.storage.save(f"files/users/nfc/{DECOMPOSED}", ContentFile(b"decomposed"))

        result = self.sync()

        f = File.objects.get(owner=self.user)
        self.assertEqual(f.content.name, f"files/users/nfc/{COMPOSED}")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("another Unicode form", result.errors[0])
        self.assertEqual(self.blobs("files/users/nfc"), sorted([COMPOSED, DECOMPOSED]))


class WebDavNameTests(NameFormsTestCase):
    def setUp(self):
        super().setUp()
        self.folder = FileService.create_folder(self.user, "Docs")
        self.file = FileService.create_file(
            self.user,
            COMPOSED,
            parent=self.folder,
            content=ContentFile(b"x", name=COMPOSED),
        )

    def test_a_decomposed_path_finds_the_composed_file(self):
        resource = WorkspaceDAVProvider().get_resource_inst(
            f"/Docs/{DECOMPOSED}", _environ(self.user)
        )

        self.assertEqual(resource._file.pk, self.file.pk)

    def test_a_decomposed_member_name_finds_the_composed_file(self):
        folder = FolderResource("/Docs", _environ(self.user), self.folder)

        self.assertEqual(folder.get_member(DECOMPOSED)._file.pk, self.file.pk)

    def test_a_put_under_the_decomposed_name_writes_the_same_file(self):
        folder = FolderResource("/Docs", _environ(self.user), self.folder)

        resource = folder.create_empty_resource(DECOMPOSED)

        self.assertEqual(resource._file.pk, self.file.pk)
        self.assertEqual(File.objects.filter(parent=self.folder).count(), 1)
