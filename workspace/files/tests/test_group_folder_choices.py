"""The group folder dialog offers the groups without a folder, and is
re-rendered with the sidebar's group folders so a group that just got one
leaves the list."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.files.services import tree

User = get_user_model()


class GroupFolderChoicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.team = Group.objects.create(name="Team")
        self.board = Group.objects.create(name="Board")
        self.user.groups.add(self.team, self.board)
        self.client.force_login(self.user)

    def _choices(self):
        html = self.client.get(
            "/files/group-folders", HTTP_X_ALPINE_REQUEST="true"
        ).content.decode()
        self.assertIn('id="group-folders-section"', html)
        start = html.index('id="group-folder-choices"')
        return html[start:]

    def test_the_refresh_carries_the_choices(self):
        choices = self._choices()
        self.assertIn(f'value="{self.team.id}"', choices)
        self.assertIn(f'value="{self.board.id}"', choices)

    def test_a_group_with_a_folder_leaves_the_choices(self):
        tree.create_folder(self.user, "Team", group=self.team)
        choices = self._choices()
        self.assertNotIn(f'value="{self.team.id}"', choices)
        self.assertIn(f'value="{self.board.id}"', choices)
