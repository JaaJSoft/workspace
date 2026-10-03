from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.core.module_registry import SearchScope
from workspace.files.models import File, FileShare
from workspace.files.search import search_files
from workspace.files.services import FileService
from workspace.files.services.search_index import index_file
from workspace.files.services.sharing import share_file

User = get_user_model()


class SearchFilesScopeTests(TestCase):
    """Mine keeps the personal tree: no group folder, not even one the user
    owns a file in, and nothing shared with them."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        bob = User.objects.create_user(username="bob", password="x")
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        team_root = FileService.create_folder(owner=bob, name="Team", group=team)

        self.personal = self._file("budget-alice.ods", self.alice)
        self.own_group_file = self._file(
            "budget-team-alice.ods", self.alice, parent=team_root, group=team
        )
        self.group_file = self._file(
            "budget-team-bob.ods", bob, parent=team_root, group=team
        )
        self.shared = self._file("budget-bob.ods", bob)
        share_file(
            self.shared,
            target_user=self.alice,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=bob,
        )

    def _file(self, name, owner, *, parent=None, group=None):
        file_obj = File.objects.create(
            owner=owner,
            name=name,
            parent=parent,
            group=group,
            node_type=File.NodeType.FILE,
        )
        index_file(file_obj)
        return file_obj

    def _names(self, scope):
        return {r.name for r in search_files("budget", self.alice, 10, scope=scope)}

    def test_all_reaches_groups_and_shares(self):
        self.assertEqual(
            self._names(SearchScope.ALL),
            {
                "budget-alice.ods",
                "budget-team-alice.ods",
                "budget-team-bob.ods",
                "budget-bob.ods",
            },
        )

    def test_mine_keeps_the_personal_tree_only(self):
        self.assertEqual(self._names(SearchScope.MINE), {"budget-alice.ods"})
