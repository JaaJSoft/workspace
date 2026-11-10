"""Where a live node's bytes belong has one definition, ``live_path``."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.files.models import File, file_upload_path
from workspace.files.services import storage_ops, tree
from workspace.files.storage_layout import live_path, user_root

User = get_user_model()


class LivePathTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.docs = tree.create_folder(self.alice, "Docs")
        self.report = tree.create_file(self.alice, "report.pdf", parent=self.docs)
        self.loose = tree.create_file(self.alice, "loose.txt")

        self.group = Group.objects.create(name="Team")
        self.alice.groups.add(self.group)
        self.team = tree.create_folder(self.alice, "Team", group=self.group)
        self.minutes = tree.create_file(self.alice, "minutes.md", parent=self.team)

    def test_a_personal_node_sits_under_its_owners_root(self):
        self.assertEqual(user_root("alice"), "files/users/alice")
        self.assertEqual(live_path(self.loose), "files/users/alice/loose.txt")
        self.assertEqual(live_path(self.docs), "files/users/alice/Docs")
        self.assertEqual(live_path(self.report), "files/users/alice/Docs/report.pdf")

    def test_a_group_node_sits_under_the_groups_root(self):
        self.assertEqual(live_path(self.team), "files/groups/Team")
        self.assertEqual(live_path(self.minutes), "files/groups/Team/minutes.md")

    def test_a_blob_named_after_its_node_is_stored_at_the_nodes_live_path(self):
        for node in (self.loose, self.report, self.minutes):
            with self.subTest(node=node.name):
                self.assertEqual(file_upload_path(node, node.name), live_path(node))

    def test_a_live_nodes_storage_path_is_its_live_path(self):
        for node in (self.loose, self.docs, self.report, self.team, self.minutes):
            with self.subTest(node=node.name):
                self.assertEqual(storage_ops.node_storage_path(node), live_path(node))

    def test_a_blob_under_another_name_lands_beside_its_node(self):
        self.assertEqual(
            file_upload_path(self.report, "upload.tmp"),
            "files/users/alice/Docs/upload.tmp",
        )

    def test_a_nested_node_with_no_stored_path_still_lands_in_its_folder(self):
        """``path`` is filled on save: a node read before it was at its
        owner's root, whatever folder it was headed for."""
        unsaved = File(
            owner=self.alice,
            name="draft.txt",
            node_type=File.NodeType.FILE,
            parent=self.docs,
        )

        self.assertEqual(
            file_upload_path(unsaved, "draft.txt"), "files/users/alice/Docs/draft.txt"
        )
