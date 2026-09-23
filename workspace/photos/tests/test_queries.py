from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import FileScan, FileTag, Tag
from workspace.files.services import FileService
from workspace.photos.queries import library_files, library_tags, unanalyzed_count

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
