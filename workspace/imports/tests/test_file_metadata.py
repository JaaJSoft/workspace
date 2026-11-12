from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.files.models import FileFavorite, FileTag, Tag
from workspace.files.services import tree
from workspace.imports.services import file_metadata

User = get_user_model()

PARAMETER_LIMIT = 40


class MarkFavoritesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.a = tree.create_folder(self.user, "a")
        self.b = tree.create_folder(self.user, "b")

    def test_favorites_every_file_once(self):
        self.assertEqual(
            file_metadata.mark_favorites(self.user, [self.a.uuid, self.b.uuid]), 2
        )
        self.assertEqual(FileFavorite.objects.filter(owner=self.user).count(), 2)

    def test_running_again_adds_nothing(self):
        file_metadata.mark_favorites(self.user, [self.a.uuid])
        self.assertEqual(
            file_metadata.mark_favorites(self.user, [self.a.uuid, self.b.uuid]), 1
        )
        self.assertEqual(FileFavorite.objects.filter(owner=self.user).count(), 2)

    def test_a_repeated_uuid_is_counted_once(self):
        self.assertEqual(
            file_metadata.mark_favorites(self.user, [self.a.uuid, self.a.uuid]), 1
        )

    def test_more_files_than_one_statement_binds(self):
        folders = [
            tree.create_folder(self.user, f"folder {index}")
            for index in range(PARAMETER_LIMIT + 20)
        ]
        uuids = [folder.uuid for folder in folders]
        file_metadata.mark_favorites(self.user, uuids[:1])

        with sqlite_parameter_limit(PARAMETER_LIMIT):
            added = file_metadata.mark_favorites(self.user, uuids)

        self.assertEqual(added, len(uuids) - 1)
        favorites = FileFavorite.objects.filter(owner=self.user, file__in=folders)
        self.assertEqual(favorites.count(), len(uuids))

    def test_nothing_to_do(self):
        self.assertEqual(file_metadata.mark_favorites(self.user, []), 0)
        self.assertFalse(FileFavorite.objects.exists())


class ApplyTagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.file = tree.create_folder(self.user, "a")

    def test_creates_the_tag_on_first_use_and_attaches_it(self):
        self.assertEqual(
            file_metadata.apply_tag(self.user, "Invoices", [self.file.uuid]), 1
        )
        tag = Tag.objects.get(owner=self.user, name="Invoices")
        self.assertTrue(FileTag.objects.filter(file=self.file, tag=tag).exists())

    def test_running_again_adds_nothing(self):
        file_metadata.apply_tag(self.user, "Invoices", [self.file.uuid])
        self.assertEqual(
            file_metadata.apply_tag(self.user, "Invoices", [self.file.uuid]), 0
        )
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(FileTag.objects.count(), 1)

    def test_more_files_than_one_statement_binds(self):
        folders = [
            tree.create_folder(self.user, f"folder {index}")
            for index in range(PARAMETER_LIMIT + 20)
        ]
        uuids = [folder.uuid for folder in folders]
        file_metadata.apply_tag(self.user, "Invoices", uuids[:1])

        with sqlite_parameter_limit(PARAMETER_LIMIT):
            added = file_metadata.apply_tag(self.user, "Invoices", uuids)

        self.assertEqual(added, len(uuids) - 1)
        self.assertEqual(FileTag.objects.filter(file__in=folders).count(), len(uuids))

    def test_an_existing_tag_is_reused_whatever_its_case(self):
        existing = Tag.objects.create(owner=self.user, name="invoices", color="#fff")
        file_metadata.apply_tag(self.user, "Invoices", [self.file.uuid])
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 1)
        self.assertTrue(FileTag.objects.filter(file=self.file, tag=existing).exists())

    def test_another_users_tag_is_not_reused(self):
        bob = User.objects.create_user(username="bob", password="pw")
        Tag.objects.create(owner=bob, name="Invoices")
        file_metadata.apply_tag(self.user, "Invoices", [self.file.uuid])
        self.assertTrue(Tag.objects.filter(owner=self.user, name="Invoices").exists())

    def test_an_overlong_name_is_truncated_to_what_the_model_accepts(self):
        file_metadata.apply_tag(self.user, "x" * 300, [self.file.uuid])
        self.assertEqual(len(Tag.objects.get(owner=self.user).name), 100)

    def test_a_blank_name_creates_nothing(self):
        self.assertEqual(file_metadata.apply_tag(self.user, "   ", [self.file.uuid]), 0)
        self.assertFalse(Tag.objects.exists())

    def test_no_file_means_no_tag_is_created(self):
        self.assertEqual(file_metadata.apply_tag(self.user, "Invoices", []), 0)
        self.assertFalse(Tag.objects.exists())
