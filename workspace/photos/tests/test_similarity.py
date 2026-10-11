from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.files.services import FileService
from workspace.photos.models import HiddenFile, PhotoEmbedding
from workspace.photos.queries import ALL, MINE
from workspace.photos.services import similarity
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.similarity import (
    search_by_description,
    similar_photos,
)

from .embeddings import BLUE, DARK_RED, RED, embeddings_on, library_photo

User = get_user_model()


def _query(text):
    """What the worker would answer for *text* (embed_query)."""
    return list(FakeEmbeddingBackend().embed_text(text))


@embeddings_on
class SimilarPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.red = library_photo(self.user, "red.jpg", RED)

    def tearDown(self):
        cache.clear()

    def _ids(self, matches):
        return [match.file_id for match in matches]

    def test_ranks_the_look_alikes_and_flags_the_near_duplicates(self):
        burst = library_photo(self.user, "red-2.jpg", RED)
        dark = library_photo(self.user, "dark.jpg", DARK_RED)
        library_photo(self.user, "blue.jpg", BLUE)

        matches = similar_photos(self.user, self.red)

        self.assertEqual(self._ids(matches), [burst.pk, dark.pk])
        self.assertEqual([m.near_duplicate for m in matches], [True, False])
        self.assertLess(matches[0].distance, matches[1].distance)

    def test_none_while_the_photo_has_no_embedding(self):
        pending = library_photo(self.user, "new.jpg", RED, embed=False)

        self.assertIsNone(similar_photos(self.user, pending))

    @override_settings(PHOTOS_SIMILAR_MAX_DISTANCE=0.05)
    def test_the_threshold_setting_narrows_the_answer(self):
        library_photo(self.user, "dark.jpg", DARK_RED)

        self.assertEqual(similar_photos(self.user, self.red), [])

    def test_never_returns_another_users_photo(self):
        bob = User.objects.create_user(username="bob", password="p")
        library_photo(bob, "red.jpg", RED)

        self.assertEqual(similar_photos(self.user, self.red, ALL), [])

    def test_trashed_and_hidden_photos_drop_out(self):
        trashed = library_photo(self.user, "trashed.jpg", RED)
        hidden = library_photo(self.user, "hidden.jpg", RED)
        FileService.soft_delete(trashed)
        HiddenFile.objects.create(owner=self.user, file=hidden)

        self.assertEqual(similar_photos(self.user, self.red), [])

        FileService.restore(trashed)
        self.assertEqual(self._ids(similar_photos(self.user, self.red)), [trashed.pk])

    def test_an_embedding_of_another_model_is_never_compared(self):
        other = library_photo(self.user, "red-2.jpg", RED)
        PhotoEmbedding.objects.filter(file=other).update(model="clip_vit_b32")

        self.assertEqual(similar_photos(self.user, self.red), [])

    def test_a_group_library_spans_every_members_photos(self):
        team = Group.objects.create(name="Team")
        bob = User.objects.create_user(username="bob", password="p")
        for member in (self.user, bob):
            member.groups.add(team)
        folder = FileService.create_folder(owner=bob, name="Team", group=team)
        bobs = library_photo(bob, "red.jpg", RED, parent=folder)

        self.assertEqual(
            self._ids(similar_photos(self.user, self.red, team)), [bobs.pk]
        )
        self.assertEqual(similar_photos(self.user, self.red, MINE), [])


@embeddings_on
class SearchByDescriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.red = library_photo(self.user, "red.jpg", RED)
        self.dark = library_photo(self.user, "dark.jpg", DARK_RED)
        self.blue = library_photo(self.user, "blue.jpg", BLUE)

    def tearDown(self):
        cache.clear()

    def _search(self, text, scope=MINE):
        with patch.object(similarity, "embed_query", side_effect=_query):
            return [m.file_id for m in search_by_description(self.user, text, scope)]

    def test_ranks_the_photos_the_text_describes(self):
        self.assertEqual(self._search("red"), [self.red.pk, self.dark.pk])
        self.assertEqual(self._search("blue sky"), [self.blue.pk])

    def test_a_text_describing_nothing_matches_nothing(self):
        self.assertEqual(self._search("birthday cake"), [])

    def test_stays_in_the_users_library(self):
        bob = User.objects.create_user(username="bob", password="p")

        with patch.object(similarity, "embed_query", side_effect=_query):
            self.assertEqual(search_by_description(bob, "red", ALL), [])
