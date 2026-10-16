"""The data migration that gives the tags automatic tagging created before
it set an icon their classic tag's icon."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import FileTag, Tag

from .images import jpeg_bytes, upload

# Migration module names are not valid identifiers, so import by string.
migration = importlib.import_module("workspace.photos.migrations.0021_auto_tag_icons")

User = get_user_model()


class AutoTagIconsMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.photo = upload(self.user, "photo.jpg", jpeg_bytes())

    def _tag(self, name, source=FileTag.Source.AUTO, icon=""):
        tag = Tag.objects.create(owner=self.user, name=name, icon=icon)
        FileTag.objects.create(file=self.photo, tag=tag, source=source)
        return tag

    def _migrate(self):
        migration.give_auto_tags_their_icon(apps, schema_editor_stub())

    def test_an_automatic_tag_without_an_icon_gets_its_classic_icon(self):
        dog = self._tag("Dog")

        self._migrate()

        dog.refresh_from_db()
        self.assertEqual(dog.icon, "dog")

    def test_a_tag_with_an_icon_of_its_own_keeps_it(self):
        dog = self._tag("dog", icon="heart")

        self._migrate()

        dog.refresh_from_db()
        self.assertEqual(dog.icon, "heart")

    def test_a_tag_only_put_by_hand_is_left_alone(self):
        dog = self._tag("Dog", source=FileTag.Source.MANUAL)

        self._migrate()

        dog.refresh_from_db()
        self.assertEqual(dog.icon, "")
