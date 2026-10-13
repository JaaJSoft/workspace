"""A tile fetches the smallest thumbnail that still covers it, not the largest."""

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import TestCase as PlainTestCase
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import File
from workspace.photos.templatetags.photos_filters import tile_srcset
from workspace.users.services.settings import set_setting

from .images import make_photo

User = get_user_model()


def _photo(width, height):
    return SimpleNamespace(media_item=SimpleNamespace(width=width, height=height))


class TileSrcsetTests(PlainTestCase):
    def test_each_size_is_described_by_its_short_side(self):
        self.assertEqual(
            tile_srcset("/t", _photo(3000, 2000)),
            "/t?size=128 85w, /t?size=256 171w, /t 341w",
        )

    def test_a_portrait_photo_is_described_by_its_width(self):
        self.assertEqual(
            tile_srcset("/t", _photo(2000, 3000)),
            "/t?size=128 85w, /t?size=256 171w, /t 341w",
        )

    def test_a_square_or_unmeasured_photo_by_the_size_itself(self):
        expected = "/t?size=128 128w, /t?size=256 256w, /t 512w"
        self.assertEqual(tile_srcset("/t", _photo(800, 800)), expected)
        self.assertEqual(tile_srcset("/t", _photo(None, None)), expected)
        self.assertEqual(tile_srcset("/t", SimpleNamespace(media_item=None)), expected)

    def test_a_url_with_a_query_keeps_it(self):
        self.assertEqual(
            tile_srcset("/t?access_token=a", _photo(800, 800)),
            "/t?access_token=a&size=128 128w, "
            "/t?access_token=a&size=256 256w, /t?access_token=a 512w",
        )


@patch("workspace.photos.services.timeline.PAGE_SIZE", 1)
class TimelineTileSizesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        for day in (14, 13):
            photo = make_photo(
                self.user, f"p{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            File.objects.filter(pk=photo.pk).update(has_thumbnail=True)
        set_setting(self.user, "photos", "tile_size", 5)

    def tearDown(self):
        cache.clear()

    def _sizes(self, response):
        html = response.content.decode()
        self.assertIn("?size=128 ", html)
        return [
            " ".join(sizes.split()) for sizes in re.findall(r'\ssizes="([^"]*)"', html)
        ]

    def test_the_first_and_the_appended_pages_size_tiles_by_the_slider(self):
        first = self.client.get("/photos")
        sentinel = re.search(r"timelineSentinel\('([^']+)'\)", first.content.decode())
        # Written through |escapejs, as a JS string literal.
        appended = self.client.get(json.loads(f'"{sentinel[1]}"'))

        expected = "auto, (min-width: 640px) 256px, calc((100vw - 24px) / 3)"
        self.assertEqual(self._sizes(first), [expected])
        self.assertEqual(self._sizes(appended), [expected])
