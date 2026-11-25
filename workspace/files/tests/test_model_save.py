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

    def test_a_rename_given_an_iterator_writes_the_name_and_the_path(self):
        self.sub.name = "Renamed"
        self.sub.save(update_fields=iter(["name"]))

        self.sub.refresh_from_db()
        self.leaf.refresh_from_db()
        self.assertEqual(self.sub.name, "Renamed")
        self.assertEqual(self.sub.path, "Docs/Renamed")
        self.assertEqual(self.leaf.path, "Docs/Renamed/Leaf")

    def test_a_metadata_save_given_an_iterator_writes_its_fields(self):
        self.sub.icon = "heart"
        with self.assertNumQueries(1):
            self.sub.save(update_fields=iter(["icon"]))

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.icon, "heart")

    def test_a_content_write_follows_an_ancestor_renamed_since_the_load(self):
        stale_leaf = File.objects.get(pk=self.leaf.pk)
        self.sub.name = "Renamed"
        self.sub.save(update_fields=["name"])

        stale_leaf.size = 0
        stale_leaf.save(update_fields=["content", "size"])

        # The blob key of the content is built from this path.
        self.assertEqual(stale_leaf.path, "Docs/Renamed/Leaf")
