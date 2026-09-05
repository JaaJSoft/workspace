from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.files.models import File, FileShareLink
from workspace.files.services.public_links import (
    MAX_UPLOAD_NAME_LENGTH,
    resolve_within,
    sanitize_upload_name,
)

User = get_user_model()


class ResolveWithinTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.other = User.objects.create_user(
            username="other", email="other@example.com", password="pass123"
        )
        self.root = File.objects.create(
            owner=self.owner, name="Shared", node_type=File.NodeType.FOLDER
        )
        self.sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.inside = File.objects.create(
            owner=self.owner,
            name="in.txt",
            node_type=File.NodeType.FILE,
            parent=self.sub,
        )
        self.outside = File.objects.create(
            owner=self.owner, name="out.txt", node_type=File.NodeType.FILE
        )
        self.link = FileShareLink.objects.create(file=self.root, created_by=self.owner)

    def test_the_root_resolves_to_itself(self):
        self.assertEqual(resolve_within(self.link, str(self.root.uuid)), self.root)

    def test_a_descendant_resolves(self):
        self.assertEqual(resolve_within(self.link, str(self.inside.uuid)), self.inside)

    def test_a_node_outside_the_subtree_does_not_resolve(self):
        self.assertIsNone(resolve_within(self.link, str(self.outside.uuid)))

    def test_a_malformed_uuid_does_not_raise(self):
        self.assertIsNone(resolve_within(self.link, "not-a-uuid"))

    def test_a_missing_uuid_does_not_resolve(self):
        self.assertIsNone(
            resolve_within(self.link, "00000000-0000-0000-0000-000000000000")
        )

    def test_a_trashed_descendant_does_not_resolve(self):
        self.inside.soft_delete()
        self.assertIsNone(resolve_within(self.link, str(self.inside.uuid)))

    def test_trashing_an_ancestor_hides_its_children(self):
        self.sub.soft_delete()
        self.assertIsNone(resolve_within(self.link, str(self.inside.uuid)))

    def test_another_users_node_with_a_colliding_path_does_not_resolve(self):
        """The prefix test alone is not enough: `path` is not globally unique."""
        their_root = File.objects.create(
            owner=self.other, name="Shared", node_type=File.NodeType.FOLDER
        )
        their_file = File.objects.create(
            owner=self.other,
            name="secret.txt",
            node_type=File.NodeType.FILE,
            parent=their_root,
        )
        self.assertTrue(their_file.path.startswith(f"{self.root.path}/"))
        self.assertIsNone(resolve_within(self.link, str(their_file.uuid)))

    def test_a_group_folder_with_a_colliding_path_does_not_resolve(self):
        """Group and personal folders share the same owner but different groups."""
        group = Group.objects.create(name="engineering")
        group_root = File.objects.create(
            owner=self.owner,
            name="Shared",
            node_type=File.NodeType.FOLDER,
            group=group,
        )
        group_file = File.objects.create(
            owner=self.owner,
            name="secret.txt",
            node_type=File.NodeType.FILE,
            parent=group_root,
            group=group,
        )
        self.assertTrue(group_file.path.startswith(f"{self.root.path}/"))
        self.assertIsNone(resolve_within(self.link, str(group_file.uuid)))


class ScopeTests(TestCase):
    """A group folder's children carry their creator as owner, not the root's."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="scope_alice", email="a@example.com", password="pass123"
        )
        self.bob = User.objects.create_user(
            username="scope_bob", email="b@example.com", password="pass123"
        )
        self.team = Group.objects.create(name="scope_team")

    def test_a_group_member_s_file_resolves_inside_a_group_folder(self):
        root = File.objects.create(
            owner=self.alice,
            name="Team",
            node_type=File.NodeType.FOLDER,
            group=self.team,
        )
        theirs = File.objects.create(
            owner=self.bob,
            name="notes.txt",
            node_type=File.NodeType.FILE,
            parent=root,
            group=self.team,
        )
        link = FileShareLink.objects.create(file=root, created_by=self.alice)
        self.assertEqual(resolve_within(link, str(theirs.uuid)), theirs)

    def test_a_personal_root_still_rejects_another_owner_s_child(self):
        root = File.objects.create(
            owner=self.alice, name="Mine", node_type=File.NodeType.FOLDER
        )
        theirs = File.objects.create(
            owner=self.bob, name="odd.txt", node_type=File.NodeType.FILE, parent=root
        )
        link = FileShareLink.objects.create(file=root, created_by=self.alice)
        self.assertIsNone(resolve_within(link, str(theirs.uuid)))


class SanitizeUploadNameTests(TestCase):
    def test_traversal_is_reduced_to_a_basename(self):
        self.assertEqual(sanitize_upload_name("../../etc/passwd"), "passwd")

    def test_windows_separators_are_reduced_too(self):
        self.assertEqual(sanitize_upload_name(r"C:\\temp\\report.pdf"), "report.pdf")

    def test_control_characters_are_stripped(self):
        self.assertEqual(sanitize_upload_name("re\x00po\x1frt.pdf"), "report.pdf")

    def test_c1_control_characters_are_stripped(self):
        self.assertEqual(sanitize_upload_name("re\x9bport.pdf"), "report.pdf")

    def test_a_long_name_is_clamped_keeping_the_extension(self):
        result = sanitize_upload_name("a" * 300 + ".pdf")
        self.assertEqual(len(result), MAX_UPLOAD_NAME_LENGTH)
        self.assertTrue(result.endswith(".pdf"))

    def test_dots_only_falls_back(self):
        self.assertEqual(sanitize_upload_name("..."), "upload")

    def test_empty_falls_back(self):
        self.assertEqual(sanitize_upload_name(""), "upload")

    def test_none_falls_back(self):
        self.assertEqual(sanitize_upload_name(None), "upload")

    def test_a_trailing_separator_falls_back(self):
        self.assertEqual(sanitize_upload_name("folder/"), "upload")

    def test_an_ordinary_name_survives(self):
        self.assertEqual(
            sanitize_upload_name("Q3 report.final.pdf"), "Q3 report.final.pdf"
        )
