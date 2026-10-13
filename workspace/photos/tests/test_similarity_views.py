"""The ranked listings: similar photos (``?similar=``) and search by
description (``?q=``), and the global search's way into the latter."""

import json
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.core.module_registry import SearchScope
from workspace.files.services import FileService
from workspace.photos.models import HiddenFile, PhotoEmbedding
from workspace.photos.search import search_by_description_entry, search_photos
from workspace.photos.services import similarity
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.photo_embeddings import QueryEmbeddingUnavailable

from .embeddings import (
    BLUE,
    DARK_RED,
    RED,
    embeddings_on,
    ffmpeg_installed,
    library_photo,
    library_video,
)
from .images import make_video

User = get_user_model()


def _query(text):
    return list(FakeEmbeddingBackend().embed_text(text))


class RankedViewTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _tiles(self, response):
        return re.findall(r'data-uuid="([0-9a-f-]{36})"', response.content.decode())


@embeddings_on
class SimilarViewTests(RankedViewTestCase):
    def setUp(self):
        super().setUp()
        self.red = library_photo(self.user, "red.jpg", RED)

    def test_lists_the_near_duplicates_then_the_similar_photos(self):
        dark = library_photo(self.user, "dark.jpg", DARK_RED)
        burst = library_photo(self.user, "burst.jpg", RED)
        library_photo(self.user, "blue.jpg", BLUE)

        response = self.client.get(f"/photos?similar={self.red.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._tiles(response), [str(burst.pk), str(dark.pk)])
        html = response.content.decode()
        self.assertLess(html.index("Near-duplicates"), html.index(str(burst.pk)))
        self.assertLess(html.index(">Similar<"), html.index(str(dark.pk)))
        self.assertIn("Similar to red.jpg", html)
        self.assertNotIn("timelineSentinel", html)

    def test_a_photo_not_analyzed_yet_says_so(self):
        pending = library_photo(self.user, "new.jpg", RED, embed=False)

        response = self.client.get(f"/photos?similar={pending.pk}")

        self.assertContains(response, "Not analyzed yet")

    def test_404_on_a_photo_the_user_cannot_open(self):
        bob = User.objects.create_user(username="bob", password="p")
        bobs = library_photo(bob, "red.jpg", RED)
        hidden = library_photo(self.user, "hidden.jpg", RED)
        HiddenFile.objects.create(owner=self.user, file=hidden)

        for uuid in (bobs.pk, hidden.pk, "not-a-uuid"):
            with self.subTest(uuid=uuid):
                response = self.client.get(f"/photos?similar={uuid}")
                self.assertEqual(response.status_code, 404)

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_404_where_the_instance_does_not_embed_photos(self):
        response = self.client.get(f"/photos?similar={self.red.pk}")

        self.assertEqual(response.status_code, 404)

    def test_the_library_tabs_keep_the_ranking(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        folder = FileService.create_folder(owner=self.user, name="Team", group=team)
        library_photo(self.user, "team.jpg", RED, parent=folder)

        response = self.client.get(f"/photos?similar={self.red.pk}&scope=all")

        self.assertContains(response, f"similar={self.red.pk}")
        self.assertEqual(len(self._tiles(response)), 1)

    def test_the_back_link_falls_back_to_the_timeline_in_scope(self):
        response = self.client.get(f"/photos?similar={self.red.pk}&scope=all")

        back = re.search(
            r'<a\s+href="([^"]*)"[^>]*data-testid="photos-ranking-back"',
            response.content.decode(),
        )
        self.assertIsNotNone(back)
        self.assertEqual(back.group(1), "/photos?scope=all")

    def _embedded_media(self, response):
        match = re.search(
            r'<script id="photos-embedded-media" type="application/json">(.*?)</script>',
            response.content.decode(),
        )
        return json.loads(match[1])

    @ffmpeg_installed()
    def test_the_menus_offer_similar_photos_on_photos_and_videos(self):
        response = self.client.get("/photos")

        self.assertContains(response, "showSimilar(ctxMenu.photo.uuid)")
        self.assertEqual(self._embedded_media(response), ["photo", "video"])

    @ffmpeg_installed(False)
    def test_without_ffmpeg_the_menus_offer_it_on_photos_only(self):
        response = self.client.get("/photos")

        self.assertEqual(self._embedded_media(response), ["photo"])

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_the_menus_offer_nothing_where_the_instance_does_not_embed(self):
        response = self.client.get("/photos")

        self.assertEqual(self._embedded_media(response), [])


@embeddings_on
class DescriptionSearchViewTests(RankedViewTestCase):
    def setUp(self):
        super().setUp()
        self.red = library_photo(self.user, "red.jpg", RED)
        self.blue = library_photo(self.user, "blue.jpg", BLUE)

    def test_ranks_the_photos_the_text_describes(self):
        with patch.object(similarity, "embed_query", side_effect=_query):
            response = self.client.get("/photos?q=a+blue+car")

        self.assertEqual(self._tiles(response), [str(self.blue.pk)])
        self.assertContains(response, "“a blue car”")
        self.assertContains(response, 'value="a blue car"')

    def test_the_back_link_falls_back_to_the_timeline_in_scope(self):
        with patch.object(similarity, "embed_query", side_effect=_query):
            response = self.client.get("/photos?q=red&scope=all")

        back = re.search(
            r'<a\s+href="([^"]*)"[^>]*data-testid="photos-ranking-back"',
            response.content.decode(),
        )
        self.assertIsNotNone(back)
        self.assertEqual(back.group(1), "/photos?scope=all")

    def test_no_match_says_so(self):
        with patch.object(similarity, "embed_query", side_effect=_query):
            response = self.client.get("/photos?q=birthday+cake")

        self.assertEqual(self._tiles(response), [])
        self.assertContains(response, "No match")

    def test_a_search_that_could_not_run_says_so(self):
        with patch.object(
            similarity, "embed_query", side_effect=QueryEmbeddingUnavailable("timeout")
        ):
            response = self.client.get("/photos?q=red")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not run right now")

    def test_a_blank_query_is_the_timeline(self):
        response = self.client.get("/photos?q=+++")

        self.assertEqual(len(self._tiles(response)), 2)
        self.assertContains(response, 'data-testid="photos-search"')

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_no_search_box_where_the_instance_does_not_embed_photos(self):
        response = self.client.get("/photos")

        self.assertNotContains(response, 'data-testid="photos-search"')
        self.assertEqual(self.client.get("/photos?q=red").status_code, 404)


@embeddings_on
class VideoRankingViewTests(RankedViewTestCase):
    """A video in a ranking: listed once, opened at the moment that matched."""

    def _tile(self, response, file_obj):
        match = re.search(
            rf'<div[^>]*data-uuid="{file_obj.pk}".*?</button>.*?(?=<div\s|$)',
            response.content.decode(),
            re.S,
        )
        return match[0]

    def test_a_video_opens_at_the_moment_that_looks_like_the_photo(self):
        red = library_photo(self.user, "red.jpg", RED)
        clip = library_video(self.user, "clip.webm", [(0.0, BLUE), (2.0, RED)])

        response = self.client.get(f"/photos?similar={red.pk}")

        self.assertEqual(self._tiles(response), [str(clip.pk)])
        tile = self._tile(response, clip)
        frame = PhotoEmbedding.objects.get(file=clip, timestamp=2.0)
        self.assertIn(f'src="/api/v1/photos/frames/{frame.pk}"', tile)
        self.assertIn('data-at="2.000"', tile)
        self.assertRegex(tile, r"Opens at</span>\s*<span>0:02 / 0:03</span>")
        self.assertContains(response, "1 video")

    def test_a_match_on_the_first_frame_opens_at_the_start(self):
        red = library_photo(self.user, "red.jpg", RED)
        clip = library_video(self.user, "clip.webm", [(0.0, RED)])

        response = self.client.get(f"/photos?similar={red.pk}")

        self.assertNotIn("data-at=", self._tile(response, clip))

    def test_a_videos_look_alikes_never_list_itself(self):
        clip = library_video(self.user, "clip.webm", [(0.0, RED), (3.0, BLUE)])
        blue = library_photo(self.user, "blue.jpg", BLUE)

        response = self.client.get(f"/photos?similar={clip.pk}")

        self.assertEqual(self._tiles(response), [str(blue.pk)])
        self.assertContains(response, "Similar to clip.webm")
        self.assertContains(response, "1 photo")

    def test_a_video_not_analyzed_yet_says_so(self):
        pending = make_video(self.user, "new.webm", None)

        response = self.client.get(f"/photos?similar={pending.pk}")

        self.assertContains(response, "This video has not been analyzed yet")

    def test_a_description_ranks_photos_and_videos_together(self):
        library_photo(self.user, "blue.jpg", BLUE)
        library_video(self.user, "clip.webm", [(0.0, RED), (2.5, BLUE)])

        with patch.object(similarity, "embed_query", side_effect=_query):
            response = self.client.get("/photos?q=blue")

        self.assertEqual(len(self._tiles(response)), 2)
        self.assertContains(response, "1 photo, 1 video")
        self.assertContains(response, 'data-at="2.500"')


@embeddings_on
class VideoFrameViewTests(RankedViewTestCase):
    def setUp(self):
        super().setUp()
        self.clip = library_video(self.user, "clip.webm", [(0.0, RED), (3.0, BLUE)])
        self.frame = PhotoEmbedding.objects.get(file=self.clip, timestamp=3.0)

    def test_serves_the_picture_of_a_key_frame(self):
        response = self.client.get(f"/api/v1/photos/frames/{self.frame.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("private", response["Cache-Control"])
        response.close()

    def test_404_to_whoever_cannot_open_the_video(self):
        User.objects.create_user(username="bob", password="p")
        self.client.login(username="bob", password="p")

        response = self.client.get(f"/api/v1/photos/frames/{self.frame.pk}")

        self.assertEqual(response.status_code, 404)

    def test_404_for_a_photo(self):
        photo = library_photo(self.user, "red.jpg", RED)
        row = PhotoEmbedding.objects.get(file=photo)

        response = self.client.get(f"/api/v1/photos/frames/{row.pk}")

        self.assertEqual(response.status_code, 404)

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_404_where_the_instance_does_not_embed(self):
        response = self.client.get(f"/api/v1/photos/frames/{self.frame.pk}")

        self.assertEqual(response.status_code, 404)


@embeddings_on
class GlobalSearchEntryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_offers_the_photos_a_query_describes(self):
        library_photo(self.user, "a.jpg", RED)

        (result,) = search_by_description_entry("dog on  the beach", self.user, 5)

        self.assertEqual(result.url, "/photos?q=dog+on+the+beach&scope=all")
        self.assertEqual(result.module_slug, "photos")
        self.assertIn(result, search_photos("dog on the beach", self.user, 10))

    def test_nothing_before_any_photo_is_embedded(self):
        library_photo(self.user, "a.jpg", RED, embed=False)

        self.assertEqual(search_by_description_entry("dog", self.user, 5), [])

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_nothing_where_the_instance_does_not_embed_photos(self):
        self.assertEqual(search_by_description_entry("dog", self.user, 5), [])

    def test_nothing_past_the_limit_or_for_a_blank_query(self):
        library_photo(self.user, "a.jpg", RED)

        self.assertEqual(search_by_description_entry("dog", self.user, 0), [])
        self.assertEqual(search_by_description_entry("   ", self.user, 5), [])

    def _team_photo(self):
        """A photo another member added to a group folder the user is in:
        embedded under that member, its owner."""
        bob = User.objects.create_user(username="bob", password="p")
        team = Group.objects.create(name="Team")
        for member in (self.user, bob):
            member.groups.add(team)
        folder = FileService.create_folder(owner=bob, name="Team", group=team)
        return library_photo(bob, "team.jpg", RED, parent=folder)

    def _entries(self, scope):
        results = search_photos("dog", self.user, 10, scope=scope)
        return [r for r in results if r.uuid.startswith("photos-description:")]

    def test_a_group_photo_someone_else_added_is_described_too(self):
        self._team_photo()

        (result,) = self._entries(SearchScope.ALL)

        self.assertEqual(result.url, "/photos?q=dog&scope=all")

    def test_my_photos_only_leave_the_group_photos_out(self):
        self._team_photo()

        self.assertEqual(self._entries(SearchScope.MINE), [])

        library_photo(self.user, "mine.jpg", RED)
        (result,) = self._entries(SearchScope.MINE)
        self.assertEqual(result.url, "/photos?q=dog&scope=mine")
