"""Two creates racing for the same name in the same folder.

The race is driven by injecting the competing create right after the losing
request decided its name was free: both requests have checked the name by
then, and only the database can still tell them apart.
"""

from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService, _names

User = get_user_model()


@contextmanager
def competitor_after_check(name, create):
    """Run *create* once the next request has found *name* free."""
    check_name_available = _names.check_name_available
    available_node_name = _names.available_node_name
    raced = []

    def race(chosen):
        if chosen == name and not raced:
            raced.append(True)
            create()

    def racing_check(owner, parent, chosen, *args, **kwargs):
        check_name_available(owner, parent, chosen, *args, **kwargs)
        race(chosen)

    def racing_pick(*args, **kwargs):
        chosen = available_node_name(*args, **kwargs)
        race(chosen)
        return chosen

    with (
        patch.object(_names, "check_name_available", racing_check),
        patch.object(_names, "available_node_name", racing_pick),
    ):
        yield raced


def read(file_obj):
    file_obj.refresh_from_db()
    with file_obj.content.open("rb") as fh:
        return fh.read()


class ConcurrentCreateServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.winner = None

    def _win(self, name="a.txt", parent=None):
        def create():
            self.winner = FileService.create_file(
                self.user,
                name,
                parent=parent,
                content=ContentFile(b"winner", name=name),
            )

        return create

    def test_the_loser_is_refused_and_the_winners_bytes_survive(self):
        with competitor_after_check("a.txt", self._win()) as raced:
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.create_file(
                    self.user, "a.txt", content=ContentFile(b"loser", name="a.txt")
                )
        self.assertTrue(raced)
        self.assertEqual(File.objects.filter(name__iexact="a.txt").count(), 1)
        self.assertEqual(read(self.winner), b"winner")

    def test_a_case_variant_loses_too(self):
        with competitor_after_check("A.TXT", self._win("a.txt")):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.create_file(
                    self.user, "A.TXT", content=ContentFile(b"loser", name="A.TXT")
                )
        self.assertEqual(read(self.winner), b"winner")

    def test_keep_both_takes_the_next_free_name(self):
        with competitor_after_check("a.txt", self._win()):
            loser = FileService.create_file(
                self.user,
                "a.txt",
                content=ContentFile(b"loser", name="a.txt"),
                keep_both=True,
            )
        self.assertEqual(loser.name, "a (Copy).txt")
        self.assertNotEqual(loser.content.name, self.winner.content.name)
        self.assertEqual(read(self.winner), b"winner")
        self.assertEqual(read(loser), b"loser")

    def test_keep_both_retries_until_a_name_sticks(self):
        # The folder holds a.txt and the batch holds a.txt and
        # "a (Copy).txt": both resolve to "a (Copy).txt" at once.
        FileService.create_file(self.user, "a.txt", content=ContentFile(b"old"))
        with competitor_after_check("a (Copy).txt", self._win("a (Copy).txt")):
            loser = FileService.create_file(
                self.user,
                "a.txt",
                content=ContentFile(b"loser", name="a.txt"),
                keep_both=True,
            )
        self.assertEqual(loser.name, "a (Copy 2).txt")
        self.assertEqual(read(self.winner), b"winner")
        self.assertEqual(read(loser), b"loser")

    def test_group_folder_members_race_for_one_name(self):
        group = Group.objects.create(name="Team")
        teammate = User.objects.create_user(username="bob", password="pass")
        for member in (self.user, teammate):
            member.groups.add(group)
        root = FileService.create_folder(self.user, "Team", group=group)

        def create():
            self.winner = FileService.create_file(
                teammate, "a.txt", parent=root, content=ContentFile(b"winner")
            )

        with competitor_after_check("a.txt", create):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.create_file(
                    self.user, "a.txt", parent=root, content=ContentFile(b"loser")
                )
        self.assertEqual(read(self.winner), b"winner")

    def test_folders_race_for_one_name(self):
        def create():
            FileService.create_folder(self.user, "Docs")

        with competitor_after_check("docs", create):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.create_folder(self.user, "docs")
        self.assertEqual(File.objects.filter(name__iexact="docs").count(), 1)


class ConcurrentUploadApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)
        self.winner = None

    def _win(self):
        self.winner = FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"winner", name="a.txt")
        )

    def _post(self, **extra):
        payload = {
            "name": "a.txt",
            "node_type": "file",
            "content": SimpleUploadedFile("a.txt", b"loser"),
            **extra,
        }
        return self.client.post("/api/v1/files", payload, format="multipart")

    def test_keep_both_uploads_land_under_two_names_and_two_blobs(self):
        with competitor_after_check("a.txt", self._win):
            response = self._post(on_conflict="rename")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "a (Copy).txt")
        loser = File.objects.get(uuid=response.data["uuid"])
        self.assertNotEqual(loser.content.name, self.winner.content.name)
        self.assertEqual(read(self.winner), b"winner")
        self.assertEqual(read(loser), b"loser")

    def test_the_default_mode_answers_the_usual_collision(self):
        with competitor_after_check("a.txt", self._win):
            response = self._post()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("same name", response.data["name"][0])
        self.assertEqual(File.objects.filter(name="a.txt").count(), 1)
        self.assertEqual(read(self.winner), b"winner")

    def test_replace_mode_writes_into_the_file_that_won(self):
        with competitor_after_check("a.txt", self._win):
            response = self._post(on_conflict="replace")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(self.winner.uuid))
        self.assertEqual(File.objects.filter(name="a.txt").count(), 1)
        self.assertEqual(read(self.winner), b"loser")


class NameTakenAnswerTests(APITestCase):
    """The 400 names what holds the name, whichever node type was posted."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)

    def test_a_file_posted_over_a_folder_names_the_folder(self):
        FileService.create_folder(self.user, "Docs")
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "docs",
                "node_type": "file",
                "content": SimpleUploadedFile("docs", b"x"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.data["name"],
            ["A folder with the same name already exists in this folder."],
        )

    def test_a_folder_posted_over_a_file_names_the_file(self):
        FileService.create_file(self.user, "Notes", content=ContentFile(b"x"))
        response = self.client.post(
            "/api/v1/files", {"name": "notes", "node_type": "folder"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.data["name"],
            ["A file with the same name already exists in this folder."],
        )
