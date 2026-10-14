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

from .embeddings import (
    BLUE,
    DARK_RED,
    GREEN,
    RED,
    embeddings_on,
    library_photo,
    library_video,
)
from .images import make_video

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

    def test_the_similar_photos_stop_after_a_screen_the_near_duplicates_do_not(self):
        bursts = {library_photo(self.user, f"red-{i}.jpg", RED).pk for i in range(2)}
        for i in range(31):
            library_photo(self.user, f"dark-{i}.jpg", DARK_RED)

        matches = similar_photos(self.user, self.red)

        self.assertEqual({m.file_id for m in matches if m.near_duplicate}, bursts)
        self.assertEqual(sum(not m.near_duplicate for m in matches), 30)

    def test_a_long_burst_leaves_room_for_the_similar_photos(self):
        bursts = {library_photo(self.user, f"red-{i}.jpg", RED).pk for i in range(3)}
        dark = library_photo(self.user, "dark.jpg", DARK_RED)

        matches = similar_photos(self.user, self.red, limit=3)

        self.assertEqual({m.file_id for m in matches if m.near_duplicate}, bursts)
        self.assertEqual(
            [m.file_id for m in matches if not m.near_duplicate], [dark.pk]
        )

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


@embeddings_on
class VideoSimilarityTests(TestCase):
    """A video ranks by its closest key frame, once, at that frame's time."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def test_a_photo_lists_a_video_once_at_its_closest_moment(self):
        red = library_photo(self.user, "red.jpg", RED)
        clip = library_video(
            self.user, "clip.webm", [(0.0, BLUE), (4.0, RED), (9.0, DARK_RED)]
        )

        (match,) = similar_photos(self.user, red)

        self.assertEqual(match.file_id, clip.pk)
        self.assertEqual(match.timestamp, 4.0)
        self.assertTrue(match.near_duplicate)

    def test_a_video_finds_what_looks_like_any_of_its_moments_but_never_itself(self):
        clip = library_video(self.user, "clip.webm", [(0.0, BLUE), (5.0, RED)])
        red = library_photo(self.user, "red.jpg", RED)
        blue = library_photo(self.user, "blue.jpg", BLUE)
        other = library_video(self.user, "other.webm", [(2.0, DARK_RED)])

        matches = similar_photos(self.user, clip)

        self.assertEqual({m.file_id for m in matches}, {red.pk, blue.pk, other.pk})
        timestamps = {m.file_id: m.timestamp for m in matches}
        self.assertIsNone(timestamps[red.pk])
        self.assertEqual(timestamps[other.pk], 2.0)

    def test_none_while_the_video_has_no_key_frame(self):
        pending = make_video(self.user, "new.webm", None)
        unreadable = library_video(self.user, "broken.webm", [])

        self.assertIsNone(similar_photos(self.user, pending))
        self.assertIsNone(similar_photos(self.user, unreadable))

    def test_search_by_description_opens_a_video_at_the_moment_it_describes(self):
        clip = library_video(self.user, "clip.webm", [(0.0, GREEN), (6.5, BLUE)])

        with patch.object(similarity, "embed_query", side_effect=_query):
            (match,) = search_by_description(self.user, "blue", MINE)

        self.assertEqual(match.file_id, clip.pk)
        self.assertEqual(match.timestamp, 6.5)
