"""The folder the viewer header names for the file on screen.

The viewer panel carries it as a hidden marker; the folders are the ones the
viewer may see, so a share never reveals the owner's private folders above it.
"""

import json
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import FileShare
from workspace.files.services import FileService

User = get_user_model()

MARKER = re.compile(
    r'data-viewer-location="([^"]*)" data-viewer-location-url="([^"]*)" '
    r'data-viewer-location-space="([^"]*)"'
)


class ViewerLocationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.photos = FileService.create_folder(self.owner, "Photos")
        self.trip = FileService.create_folder(self.owner, "Brittany 2019", self.photos)
        self.file = self._file("beach.txt", self.trip)

    def _file(self, name, parent=None, owner=None):
        return FileService.create_file(
            owner or self.owner,
            name,
            parent,
            content=ContentFile(b"bytes", name=name),
        )

    def _location(self, user, file_obj):
        self.client.force_login(user)
        resp = self.client.get(f"/files/view/{file_obj.uuid}")
        self.assertEqual(resp.status_code, 200)
        match = MARKER.search(resp.content.decode())
        if match is None:
            return None
        labels, url, space = match.groups()
        return (
            json.loads(labels.replace("&quot;", '"')),
            url.replace("&amp;", "&"),
            space,
        )

    def test_the_owner_sees_every_folder_and_a_link_to_the_file_in_files(self):
        self.assertEqual(
            self._location(self.owner, self.file),
            (
                ["My Files", "Photos", "Brittany 2019"],
                f"/files/{self.trip.uuid}?open={self.file.uuid}",
                "personal",
            ),
        )

    def test_a_file_at_the_root_links_to_the_root(self):
        loose = self._file("loose.txt")

        self.assertEqual(
            self._location(self.owner, loose),
            (["My Files"], f"/files?open={loose.uuid}", "personal"),
        )

    def test_a_group_file_starts_at_the_group_folder(self):
        group = Group.objects.create(name="Family")
        self.owner.groups.add(group)
        self.other.groups.add(group)
        root = FileService.create_folder(self.owner, "Family", group=group)
        wedding = FileService.create_folder(self.owner, "Wedding", root)
        photo = self._file("cake.txt", wedding)

        self.assertEqual(
            self._location(self.other, photo),
            (
                ["Family", "Wedding"],
                f"/files/{wedding.uuid}?open={photo.uuid}",
                "group",
            ),
        )

    def test_a_shared_file_hides_the_owner_folders(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.other,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertEqual(
            self._location(self.other, self.file), (["Shared with me"], "", "shared")
        )

    def test_a_trashed_file_names_no_folder(self):
        FileService.soft_delete(self.file, acting_user=self.owner)

        self.assertIsNone(self._location(self.owner, self.file))
