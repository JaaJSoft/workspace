"""A file's blob sits under its name exactly, and one live name holds one blob."""

import importlib
import os

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services._names import NameTaken
from workspace.files.sync import FileSyncService

resolve_caseless = importlib.import_module(
    "workspace.files.migrations.0066_resolve_caseless_names"
)
name_blobs = importlib.import_module(
    "workspace.files.migrations.0068_name_blobs_after_their_rows"
)

User = get_user_model()

COMPOSED = "Été.txt"  # "Été.txt", each accent one code point
DECOMPOSED = "Été.txt"  # the same name as a macOS client spells it


def read(file_obj):
    file_obj.refresh_from_db()
    with file_obj.content.open("rb") as fh:
        return fh.read()


class BlobNameTests(IsolatedMediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="pass")
        self.folder = FileService.create_folder(self.user, "Docs")

    def _create(self, name, data, **kwargs):
        return FileService.create_file(
            self.user, name, parent=self.folder, content=ContentFile(data), **kwargs
        )

    def test_a_file_is_stored_under_its_own_name(self):
        created = self._create("my doc (1).txt", b"x")

        self.assertEqual(created.content.name, "files/users/alice/Docs/my doc (1).txt")

    def test_names_the_storage_used_to_merge_each_keep_their_bytes(self):
        spaced = self._create("a b.txt", b"spaced")
        underscored = self._create("a_b.txt", b"underscored")

        self.assertEqual(read(spaced), b"spaced")
        self.assertEqual(read(underscored), b"underscored")

    def test_a_rename_to_a_name_the_storage_used_to_merge_keeps_both(self):
        spaced = self._create("a b.txt", b"spaced")
        other = self._create("x.txt", b"x")

        FileService.rename(other, "a_b.txt")

        self.assertEqual(read(spaced), b"spaced")
        self.assertEqual(read(other), b"x")

    def test_a_backslash_cannot_reach_into_a_sibling_folder(self):
        sub = FileService.create_folder(self.user, "a", parent=self.folder)
        victim = FileService.create_file(
            self.user, "b.txt", parent=sub, content=ContentFile(b"victim")
        )

        with self.assertRaisesMessage(ValueError, "backslash"):
            self._create("a\\b.txt", b"evil")

        self.assertEqual(read(victim), b"victim")

    def test_a_row_named_before_the_backslash_rule_still_saves(self):
        legacy = self._create("legacy.txt", b"x")
        File.objects.filter(pk=legacy.pk).update(name="a\\b.txt")
        legacy.refresh_from_db()

        legacy.save(update_fields=["deleted_at"])

    def test_the_disk_sync_recognises_a_file_whose_name_has_a_space(self):
        created = self._create("my doc.txt", b"x")

        result = FileSyncService().sync_user_recursive(self.user)

        created.refresh_from_db()
        self.assertIsNone(created.deleted_at)
        self.assertEqual((result.files_created, result.files_soft_deleted), (0, 0))

    def test_new_content_lands_under_the_file_name_not_the_upload_name(self):
        created = self._create("report.txt", b"old")

        FileService.update_content(created, ContentFile(b"new", name="upload.bin"))

        self.assertEqual(created.content.name, "files/users/alice/Docs/report.txt")
        self.assertEqual(read(created), b"new")

    def test_two_encodings_of_one_name_are_one_name(self):
        self._create(COMPOSED, b"composed")

        with self.assertRaises(NameTaken):
            self._create(DECOMPOSED, b"decomposed")

    def test_a_non_ascii_case_variant_is_the_same_name(self):
        self._create(COMPOSED, b"upper")

        with self.assertRaises(NameTaken):
            self._create(COMPOSED.lower(), b"lower")

    def test_keep_both_picks_a_copy_name_past_a_caseless_clash(self):
        self._create(COMPOSED, b"composed")

        kept = self._create(DECOMPOSED, b"decomposed", keep_both=True)

        self.assertEqual(kept.name, "Été (Copy).txt")
        self.assertEqual(read(kept), b"decomposed")

    def test_a_rename_refreshes_the_key_the_constraint_compares(self):
        renamed = self._create("a.txt", b"x")

        FileService.rename(renamed, COMPOSED)

        renamed.refresh_from_db()
        self.assertEqual(renamed.name_key, "été.txt")
        with self.assertRaises(IntegrityError), transaction.atomic():
            File.objects.create(
                owner=self.user, name=DECOMPOSED, node_type="file", parent=self.folder
            )


class ResolveCaselessNamesMigrationTests(TestCase):
    """Names the ``lower(name)`` constraints let through side by side."""

    def setUp(self):
        # Rolled back with the test: DDL is transactional on both backends.
        with connection.cursor() as cursor:
            for name in File.LIVE_NAME_CONSTRAINTS:
                cursor.execute(f'DROP INDEX "{name}"')
        self.user = User.objects.create_user(username="alice", password="pass")

    def _row(self, name, data, **fields):
        return File.objects.create(
            owner=self.user,
            name=name,
            node_type="file",
            content=ContentFile(data, name=name),
            **fields,
        )

    def test_the_oldest_keeps_its_name_and_the_other_encoding_becomes_a_copy(self):
        keeper = self._row(COMPOSED, b"one")
        variant = self._row(DECOMPOSED, b"two")

        resolve_caseless.resolve_caseless_names(apps, schema_editor_stub())

        keeper.refresh_from_db()
        variant.refresh_from_db()
        self.assertEqual(keeper.name, COMPOSED)
        self.assertEqual(variant.name, "Été (Copy).txt")
        self.assertEqual(variant.name_key, "été (copy).txt")
        self.assertEqual(read(keeper), b"one")
        self.assertEqual(read(variant), b"two")

    def test_names_that_differ_once_folded_are_left_alone(self):
        self._row("a.txt", b"a")
        self._row("b.txt", b"b")

        resolve_caseless.resolve_caseless_names(apps, schema_editor_stub())

        self.assertEqual(
            sorted(File.objects.values_list("name", flat=True)), ["a.txt", "b.txt"]
        )


class NameBlobsAfterTheirRowsMigrationTests(IsolatedMediaRootMixin, TestCase):
    """Blobs a create stored under the storage's rewrite of their name."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="pass")
        self.storage = File._meta.get_field("content").storage

    def _blob(self, path, data):
        return self.storage.save(path, ContentFile(data))

    def _row(self, name, content, **fields):
        return File.objects.create(
            owner=self.user, name=name, node_type="file", content=content, **fields
        )

    def _migrate(self):
        name_blobs.name_blobs_after_their_rows(apps, schema_editor_stub())

    def test_a_rewritten_blob_moves_to_its_name(self):
        row = self._row("a b.txt", self._blob("files/users/alice/a_b.txt", b"x"))

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, "files/users/alice/a b.txt")
        self.assertEqual(read(row), b"x")
        self.assertFalse(self.storage.exists("files/users/alice/a_b.txt"))

    def test_a_blob_another_row_reads_is_copied(self):
        shared = self._blob("files/users/alice/a_b.txt", b"x")
        spaced = self._row("a b.txt", shared)
        underscored = self._row("a_b.txt", shared)

        self._migrate()

        spaced.refresh_from_db()
        underscored.refresh_from_db()
        self.assertEqual(spaced.content.name, "files/users/alice/a b.txt")
        self.assertEqual(underscored.content.name, "files/users/alice/a_b.txt")
        self.assertEqual(read(spaced), b"x")
        self.assertEqual(read(underscored), b"x")

    def test_a_trashed_blob_is_renamed_inside_its_trash_directory(self):
        row = self._row(
            "a b.txt",
            self._blob("trash/users/alice/some-uuid/a_b.txt", b"x"),
            deleted_at=timezone.now(),
        )

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, "trash/users/alice/some-uuid/a b.txt")
        self.assertEqual(read(row), b"x")

    def test_a_destination_holding_something_else_is_left_alone(self):
        stray = self._blob("files/users/alice/a b.txt", b"stray")
        row = self._row("a b.txt", self._blob("files/users/alice/a_b.txt", b"x"))

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, "files/users/alice/a_b.txt")
        self.assertEqual(read(row), b"x")
        with self.storage.open(stray, "rb") as fh:
            self.assertEqual(fh.read(), b"stray")

    def test_a_blob_already_under_its_name_stays(self):
        path = self._blob("files/users/alice/a b.txt", b"x")
        row = self._row("a b.txt", path)
        before = os.path.getmtime(self.storage.path(path))

        self._migrate()

        row.refresh_from_db()
        self.assertEqual(row.content.name, path)
        self.assertEqual(os.path.getmtime(self.storage.path(path)), before)


class BackslashNameApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)

    def test_an_upload_named_with_a_backslash_is_a_400(self):
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "a" + chr(92) + "b.txt",
                "node_type": "file",
                "content": SimpleUploadedFile("b.txt", b"x"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(File.objects.filter(node_type="file").exists())
