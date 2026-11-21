"""The data migration that remembers which classic tag each tag automatic
tagging placed before it stands for, so a French owner's next dog photo lands
on the "Dog" their older photos carry instead of a new "Chien"."""

import importlib
from datetime import UTC, datetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import FileTag, Tag
from workspace.photos.models import ClassicTagMapping

from .images import jpeg_bytes, upload

# Migration module names are not valid identifiers, so import by string.
migration = importlib.import_module(
    "workspace.photos.migrations.0027_classic_tag_mapping"
)

User = get_user_model()


class ClassicTagMappingMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.photo = upload(self.user, "photo.jpg", jpeg_bytes())

    def _tag(self, name, source=FileTag.Source.AUTO, owner=None, photo=None):
        tag = Tag.objects.create(owner=owner or self.user, name=name)
        FileTag.objects.create(file=photo or self.photo, tag=tag, source=source)
        return tag

    def _migrate(self):
        migration.map_auto_tags_to_their_classic_tag(apps, schema_editor_stub())

    def _mappings(self):
        return set(
            ClassicTagMapping.objects.values_list("owner__username", "label", "tag")
        )

    def test_an_automatic_tag_is_mapped_to_its_classic_tag(self):
        dog = self._tag("dog")
        flowers = self._tag("Flowers")

        self._migrate()

        self.assertEqual(
            self._mappings(),
            {("alice", "Dog", dog.pk), ("alice", "Flower", flowers.pk)},
        )

    def test_a_tag_only_put_by_hand_is_left_alone(self):
        self._tag("Dog", source=FileTag.Source.MANUAL)

        self._migrate()

        self.assertEqual(self._mappings(), set())

    def test_a_tag_of_another_name_is_left_alone(self):
        self._tag("Doggo")

        self._migrate()

        self.assertEqual(self._mappings(), set())

    def test_each_owner_gets_their_own_mapping(self):
        bob = User.objects.create_user(username="bob", password="p")
        mine = self._tag("Dog")
        theirs = self._tag("Dog", owner=bob, photo=upload(bob, "b.jpg", jpeg_bytes()))

        self._migrate()

        self.assertEqual(
            self._mappings(), {("alice", "Dog", mine.pk), ("bob", "Dog", theirs.pk)}
        )

    def test_the_oldest_spelling_wins(self):
        self._tag("DOG")
        first = self._tag("Dog")
        Tag.objects.filter(pk=first.pk).update(
            created_at=datetime(2020, 1, 1, tzinfo=UTC)
        )

        self._migrate()

        self.assertEqual(self._mappings(), {("alice", "Dog", first.pk)})
