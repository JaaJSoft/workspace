from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import FileScan, FileTag, Tag
from workspace.files.services import FileService
from workspace.photos.queries import (
    ALL,
    MINE,
    library_files,
    library_groups,
    library_tags,
    unanalyzed_count,
)

from .images import make_photo, upload

User = get_user_model()


def _at(*args):
    return datetime(*args, tzinfo=UTC)


class LibraryFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_analyzed_live_personal_images(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14))
        upload(self.user, "unanalyzed.jpg")
        trashed = make_photo(self.user, "trashed.jpg", _at(2024, 7, 14))
        FileService.soft_delete(trashed, acting_user=self.user)

        self.assertEqual(list(library_files(self.user)), [photo])

    def test_a_row_left_on_a_file_that_is_no_longer_an_image_is_ignored(self):
        f = make_photo(self.user, "a.jpg", _at(2024, 7, 14))
        type(f).objects.filter(pk=f.pk).update(type="text")

        self.assertEqual(list(library_files(self.user)), [])


class LibraryTagsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_tags_on_photos_with_their_photo_count(self):
        summer = Tag.objects.create(owner=self.user, name="Summer")
        work = Tag.objects.create(owner=self.user, name="Work")
        for name in ("a.jpg", "b.jpg"):
            FileTag.objects.create(
                file=make_photo(self.user, name, _at(2024, 7, 14)), tag=summer
            )
        # A tagged document counts neither for Summer nor as a reason to list Work.
        FileTag.objects.create(file=upload(self.user, "notes.txt", b"x"), tag=summer)
        FileTag.objects.create(file=upload(self.user, "plan.txt", b"x"), tag=work)

        self.assertEqual(
            [(t.name, t.photo_count) for t in library_tags(self.user)],
            [("Summer", 2)],
        )


class UnanalyzedCountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_counts_the_users_images_without_a_row(self):
        upload(self.user, "a.jpg")
        upload(self.user, "b.jpg")
        make_photo(self.user, "done.jpg", _at(2024, 7, 14))
        upload(self.user, "notes.txt", b"x")
        upload(User.objects.create_user(username="bob", password="p"), "c.jpg")

        self.assertEqual(unanalyzed_count(self.user), 2)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_image_is_not_waiting_for_anything(self):
        f = upload(self.user, "bad.jpg")
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertEqual(unanalyzed_count(self.user), 0)


class ScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        bob = User.objects.create_user(username="bob", password="p")
        self.family = Group.objects.create(name="Family")
        self.team = Group.objects.create(name="Team")
        self.strangers = Group.objects.create(name="Strangers")
        self.user.groups.add(self.family, self.team)
        self.mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14))
        self.family_photo = self._group_photo(bob, self.family, "family.jpg")
        self.strangers_photo = self._group_photo(bob, self.strangers, "other.jpg")

    def _group_photo(self, owner, group, name):
        root = FileService.create_folder(owner=owner, name=group.name, group=group)
        return make_photo(owner, name, _at(2024, 7, 14), parent=root)

    def test_mine_is_the_default(self):
        self.assertEqual(list(library_files(self.user)), [self.mine])
        self.assertEqual(list(library_files(self.user, MINE)), [self.mine])

    def test_all_adds_the_users_group_folders(self):
        self.assertEqual(
            set(library_files(self.user, ALL)), {self.mine, self.family_photo}
        )

    def test_a_group_reads_that_group_alone(self):
        self.assertEqual(
            list(library_files(self.user, self.family)), [self.family_photo]
        )
        self.assertEqual(list(library_files(self.user, self.team)), [])

    def test_a_group_the_user_is_not_in_reads_as_empty(self):
        self.assertEqual(list(library_files(self.user, self.strangers)), [])

    def test_library_groups_are_the_users_groups_holding_photos(self):
        self.assertEqual(list(library_groups(self.user)), [self.family])

    def test_unanalyzed_count_follows_the_scope(self):
        root = self.family_photo.parent
        upload(User.objects.get(username="bob"), "fresh.jpg", parent=root)

        self.assertEqual(unanalyzed_count(self.user), 0)
        self.assertEqual(unanalyzed_count(self.user, ALL), 1)
        self.assertEqual(unanalyzed_count(self.user, self.family), 1)
