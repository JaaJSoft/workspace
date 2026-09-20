from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.people.services.avatar import (
    avatar_etag,
    avatar_path,
    delete_avatar,
    save_avatar,
)
from workspace.people.services.persons import create_person, delete_person

User = get_user_model()


def png_file(size=(64, 64)):
    buf = BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "avatar.png"
    return buf


class AvatarServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.person = create_person(owner=self.alice, display_name="Bob")

    def test_save_avatar_writes_webp_and_flag(self):
        save_avatar(self.person, png_file(), 0, 0, 64, 64)
        self.person.refresh_from_db()
        self.assertTrue(self.person.has_avatar)
        path = avatar_path(self.person)
        self.assertEqual(path, f"people/avatars/{self.person.uuid}.webp")
        self.assertTrue(default_storage.exists(path))
        with default_storage.open(path, "rb") as f:
            self.assertEqual(Image.open(f).format, "WEBP")
        self.assertIsNotNone(avatar_etag(self.person))

    def test_reupload_rewrites_the_row(self):
        # The blob keeps its path, so the row's `updated_at` is what the
        # listing's cache-busting query parameter reads.
        save_avatar(self.person, png_file(), 0, 0, 64, 64)
        self.person.refresh_from_db()
        first = self.person.updated_at
        save_avatar(self.person, png_file(size=(32, 32)), 0, 0, 32, 32)
        self.person.refresh_from_db()
        self.assertGreater(self.person.updated_at, first)

    def test_delete_avatar_removes_blob_and_flag(self):
        save_avatar(self.person, png_file(), 0, 0, 64, 64)
        delete_avatar(self.person)
        self.person.refresh_from_db()
        self.assertFalse(self.person.has_avatar)
        self.assertFalse(default_storage.exists(avatar_path(self.person)))
        self.assertIsNone(avatar_etag(self.person))

    def test_delete_person_removes_avatar_blob(self):
        save_avatar(self.person, png_file(), 0, 0, 64, 64)
        path = avatar_path(self.person)
        delete_person(self.person)
        self.assertFalse(default_storage.exists(path))
