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

    def test_keep_both_counts_on_from_the_name_that_was_asked_for(self):
        FileService.create_file(self.user, "a.txt", content=ContentFile(b"old"))

        def create():
            FileService.create_file(
                self.user, "a (Copy).txt", content=ContentFile(b"w")
            )

        with competitor_after_check("a (Copy).txt", create):
            response = self._post(on_conflict="rename")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "a (Copy 2).txt")

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


def read_blob(file_obj):
    """The bytes at *file_obj*'s path, whether or not its row survived."""
    with file_obj.content.storage.open(file_obj.content.name, "rb") as fh:
        return fh.read()


class ConcurrentRenameAndMoveTests(TestCase):
    """A rename or a move loses the name: neither file's bytes may suffer.

    The competitor is created inside the losing request's transaction, so its
    row goes when that request rolls back; its bytes are read off storage.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.winner = None

    def _win(self, name, parent=None, owner=None):
        def create():
            self.winner = FileService.create_file(
                owner or self.user,
                name,
                parent=parent,
                content=ContentFile(b"winner"),
            )

        return create

    def test_a_rename_that_loses_leaves_both_files_whole(self):
        mine = FileService.create_file(self.user, "b.txt", content=ContentFile(b"mine"))
        with competitor_after_check("c.txt", self._win("c.txt")):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.rename(mine, "c.txt", acting_user=self.user)
        mine = File.objects.get(pk=mine.pk)
        self.assertEqual(mine.name, "b.txt")
        self.assertEqual(read(mine), b"mine")
        self.assertEqual(read_blob(self.winner), b"winner")

    def test_a_folder_rename_that_loses_keeps_its_contents(self):
        folder = FileService.create_folder(self.user, "Old")
        inside = FileService.create_file(
            self.user, "x.txt", parent=folder, content=ContentFile(b"inside")
        )

        def create():
            FileService.create_folder(self.user, "New")

        with competitor_after_check("New", create):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.rename(folder, "New", acting_user=self.user)
        self.assertEqual(read(inside), b"inside")

    def test_a_move_into_a_group_folder_that_loses_leaves_both_files_whole(self):
        group = Group.objects.create(name="Team")
        teammate = User.objects.create_user(username="bob", password="pass")
        for member in (self.user, teammate):
            member.groups.add(group)
        root = FileService.create_folder(self.user, "Team", group=group)
        mine = FileService.create_file(self.user, "y.txt", content=ContentFile(b"mine"))

        with competitor_after_check("y.txt", self._win("y.txt", root, teammate)):
            with self.assertRaisesMessage(ValueError, "same name"):
                FileService.move(mine, root, acting_user=self.user)
        mine = File.objects.get(pk=mine.pk)
        self.assertIsNone(mine.parent_id)
        self.assertEqual(read(mine), b"mine")
        self.assertEqual(read_blob(self.winner), b"winner")

    def test_a_move_that_wins_carries_a_folder_and_its_contents(self):
        target = FileService.create_folder(self.user, "Target")
        folder = FileService.create_folder(self.user, "Docs")
        inside = FileService.create_file(
            self.user, "x.txt", parent=folder, content=ContentFile(b"inside")
        )
        FileService.move(folder, target, acting_user=self.user)
        inside = File.objects.get(pk=inside.pk)
        self.assertEqual(inside.path, "Target/Docs/x.txt")
        self.assertEqual(inside.content.name, "files/users/alice/Target/Docs/x.txt")
        self.assertEqual(read(inside), b"inside")


class BytesOutsideTheTransactionTests(TestCase):
    """The blob is written before the INSERT's transaction takes the lock."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")

    def _depth_at_each_write(self, create):
        from django.core.files.storage import FileSystemStorage
        from django.db import connection

        baseline = len(connection.atomic_blocks)
        depths = []
        original_save = FileSystemStorage._save

        def recording_save(storage, name, content):
            depths.append(len(connection.atomic_blocks) - baseline)
            return original_save(storage, name, content)

        with patch.object(FileSystemStorage, "_save", recording_save):
            created = create()
        return created, depths

    def test_an_upload_is_written_before_its_row(self):
        created, depths = self._depth_at_each_write(
            lambda: FileService.create_file(
                self.user, "big.bin", content=ContentFile(b"x" * 1024)
            )
        )
        self.assertEqual(depths, [0])
        self.assertEqual(read(created), b"x" * 1024)

    def test_a_copy_is_written_before_its_row(self):
        source = FileService.create_file(
            self.user, "big.bin", content=ContentFile(b"y" * 1024)
        )
        folder = FileService.create_folder(self.user, "Copies")
        copied, depths = self._depth_at_each_write(
            lambda: FileService.copy(source, folder, self.user)
        )
        self.assertEqual(depths, [0])
        self.assertEqual(read(copied), b"y" * 1024)
