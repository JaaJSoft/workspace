"""The viewer panel opened on its own (new tab, reload) leads to the page that
shows it, not to a bare fragment."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import FileShare
from workspace.files.services import trash, tree

User = get_user_model()


class ViewerOpenedDirectlyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.folder = tree.create_folder(self.owner, "Docs")
        self.file = self._file("notes.txt", self.folder)

    def _file(self, name, parent=None):
        return tree.create_file(
            self.owner, name, parent, content=ContentFile(b"text", name=name)
        )

    def _open(self, user, file_obj):
        self.client.force_login(user)
        return self.client.get(f"/files/view/{file_obj.uuid}")

    def test_a_file_opens_over_its_folder(self):
        resp = self._open(self.owner, self.file)
        self.assertRedirects(
            resp,
            f"/files/{self.folder.uuid}?open={self.file.uuid}",
            fetch_redirect_response=False,
        )

    def test_a_file_at_the_root_opens_over_my_files(self):
        loose = self._file("loose.txt")
        resp = self._open(self.owner, loose)
        self.assertRedirects(
            resp, f"/files?open={loose.uuid}", fetch_redirect_response=False
        )

    def test_a_shared_file_opens_over_shared_with_me(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.other
        )
        resp = self._open(self.other, self.file)
        self.assertRedirects(
            resp,
            f"/files?shared=1&open={self.file.uuid}",
            fetch_redirect_response=False,
        )

    def test_a_trashed_file_opens_over_the_trash(self):
        trash.soft_delete(self.file, acting_user=self.owner)
        resp = self._open(self.owner, self.file)
        self.assertRedirects(
            resp, f"/files/trash?open={self.file.uuid}", fetch_redirect_response=False
        )

    def test_a_file_the_user_cannot_open_is_not_found(self):
        self.assertEqual(self._open(self.other, self.file).status_code, 404)

    def test_the_panel_is_still_served_to_alpine(self):
        self.client.force_login(self.owner)
        resp = self.client.get(
            f"/files/view/{self.file.uuid}", HTTP_X_ALPINE_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 200)
