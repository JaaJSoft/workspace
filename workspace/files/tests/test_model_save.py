"""File.save() keeps ``path`` in step with the name and the parent."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import tree

User = get_user_model()


class FileSavePathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="saver", password="pw")
        self.docs = tree.create_folder(self.user, "Docs")
        self.sub = tree.create_folder(self.user, "Sub", self.docs)
        self.leaf = tree.create_folder(self.user, "Leaf", self.sub)

    def test_a_save_writing_neither_name_nor_parent_is_a_single_update(self):
        self.sub.icon = "heart"
        with self.assertNumQueries(1):
            self.sub.save(update_fields=["icon"])

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.icon, "heart")
        self.assertEqual(self.sub.path, "Docs/Sub")

    def test_a_rename_rewrites_the_descendant_paths(self):
        self.sub.name = "Renamed"
        self.sub.save(update_fields=["name"])

        self.sub.refresh_from_db()
        self.leaf.refresh_from_db()
        self.assertEqual(self.sub.path, "Docs/Renamed")
        self.assertEqual(self.sub.name_key, "renamed")
        self.assertEqual(self.leaf.path, "Docs/Renamed/Leaf")

    def test_a_new_parent_rewrites_the_descendant_paths(self):
        other = tree.create_folder(self.user, "Other")
        self.sub.parent = other
        self.sub.save(update_fields=["parent"])

        self.leaf.refresh_from_db()
        self.assertEqual(self.leaf.path, "Other/Sub/Leaf")

    def test_a_row_without_a_path_gets_one_on_any_save(self):
        File.objects.filter(pk=self.sub.pk).update(path="")
        self.sub.refresh_from_db()
        self.sub.icon = "heart"
        self.sub.save(update_fields=["icon"])

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.path, "Docs/Sub")
