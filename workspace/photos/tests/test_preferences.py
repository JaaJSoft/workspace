import json
import re
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.files.services import FileService
from workspace.photos.models import MediaItem
from workspace.photos.services.preferences import (
    display_preferences,
    is_scope_token,
)
from workspace.photos.templatetags.photos_filters import tile_aspect
from workspace.users.services.settings import set_setting

from .images import make_photo, make_video

User = get_user_model()


def _at(*args):
    return datetime(*args, tzinfo=UTC)


class PreferencesTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _tiles(self, response):
        return re.findall(r'data-uuid="([0-9a-f-]{36})"', response.content.decode())


class DisplayPreferencesTests(PreferencesTestCase):
    def test_defaults(self):
        self.assertEqual(
            display_preferences(self.user),
            {
                "tile_shape": "square",
                "tile_badges": True,
                "video_hover_preview": False,
                "default_scope": "mine",
                "default_media_type": "all",
                "import_by_date": False,
            },
        )

    def test_stored_values(self):
        for key, value in {
            "tile_shape": "original",
            "tile_badges": False,
            "video_hover_preview": True,
            "default_scope": "group:4",
            "default_media_type": "video",
            "import_by_date": True,
        }.items():
            set_setting(self.user, "photos", key, value)

        prefs = display_preferences(self.user)

        self.assertEqual(prefs["tile_shape"], "original")
        self.assertIs(prefs["tile_badges"], False)
        self.assertIs(prefs["video_hover_preview"], True)
        self.assertEqual(prefs["default_scope"], "group:4")
        self.assertEqual(prefs["default_media_type"], "video")
        self.assertIs(prefs["import_by_date"], True)

    def test_a_malformed_value_reads_as_the_default(self):
        """Written before the API checked it, or by hand: never a broken page."""
        for key, value in {
            "tile_shape": "round",
            "tile_badges": "false",
            "video_hover_preview": 1,
            "default_scope": "group:abc",
            "default_media_type": ["photo"],
            "import_by_date": "yes",
        }.items():
            set_setting(self.user, "photos", key, value)

        self.assertEqual(
            display_preferences(self.user),
            {
                "tile_shape": "square",
                "tile_badges": True,
                "video_hover_preview": False,
                "default_scope": "mine",
                "default_media_type": "all",
                "import_by_date": False,
            },
        )

    def test_scope_tokens(self):
        for token in ("mine", "all", "shared", "group:1", "group:250"):
            self.assertTrue(is_scope_token(token), token)
        for token in ("", "group:", "group:-1", "groups:1", "Mine", None, 3):
            self.assertFalse(is_scope_token(token), token)

    def test_a_group_id_int_cannot_read_is_no_scope_token(self):
        """A superscript two is a digit to str.isdigit, not to int()."""
        self.assertFalse(is_scope_token("group:²"))


class DefaultScopeTests(PreferencesTestCase):
    def setUp(self):
        super().setUp()
        self.bob = User.objects.create_user(username="bob", password="p")
        self.family = Group.objects.create(name="Family")
        self.user.groups.add(self.family)
        self.mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14, 12))
        root = FileService.create_folder(
            owner=self.bob, name="Family", group=self.family
        )
        self.family_photo = make_photo(
            self.bob, "family.jpg", _at(2024, 7, 13, 12), parent=root
        )

    def test_the_timeline_opens_on_the_default_library(self):
        set_setting(self.user, "photos", "default_scope", f"group:{self.family.pk}")

        response = self.client.get("/photos")

        self.assertEqual(self._tiles(response), [str(self.family_photo.uuid)])

    def test_links_spell_out_mine_when_it_is_not_the_default(self):
        """No ?scope= means the default library, so Mine needs its own token."""
        set_setting(self.user, "photos", "default_scope", "all")

        response = self.client.get("/photos")

        tabs = {tab["label"]: tab for tab in response.context["scope_tabs"]}
        self.assertEqual(tabs["Mine"]["url"], "/photos?scope=mine")
        self.assertEqual(tabs["All"]["url"], "/photos")
        self.assertTrue(tabs["All"]["active"])
        self.assertEqual(
            self._tiles(self.client.get(tabs["Mine"]["url"])), [str(self.mine.uuid)]
        )

    def test_the_next_page_stays_in_the_library_on_screen(self):
        set_setting(self.user, "photos", "default_scope", "all")

        response = self.client.get("/photos?scope=mine")

        self.assertEqual(response.context["timeline_url"], "/photos?scope=mine")
        self.assertEqual(
            response.context["favorites_url"], "/photos?scope=mine&favorites=1"
        )

    def test_a_group_the_user_left_falls_back_to_mine(self):
        set_setting(self.user, "photos", "default_scope", f"group:{self.family.pk}")
        self.user.groups.remove(self.family)

        response = self.client.get("/photos")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._tiles(response), [str(self.mine.uuid)])

    def test_an_explicit_group_the_user_is_not_in_is_still_404(self):
        set_setting(self.user, "photos", "default_scope", "all")

        response = self.client.get("/photos?scope=group:999")

        self.assertEqual(response.status_code, 404)

    def test_a_group_id_int_cannot_read_is_404(self):
        response = self.client.get("/photos", {"scope": "group:²"})

        self.assertEqual(response.status_code, 404)


class DefaultMediaTypeTests(PreferencesTestCase):
    def setUp(self):
        super().setUp()
        self.photo = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        self.video = make_video(self.user, "surf.webm", _at(2024, 7, 13, 12))

    def test_the_timeline_opens_on_the_default_media_type(self):
        set_setting(self.user, "photos", "default_media_type", "video")

        response = self.client.get("/photos")

        self.assertEqual(self._tiles(response), [str(self.video.uuid)])

    def test_type_all_shows_both_kinds(self):
        set_setting(self.user, "photos", "default_media_type", "video")

        response = self.client.get("/photos?type=all")

        self.assertEqual(
            self._tiles(response), [str(self.photo.uuid), str(self.video.uuid)]
        )

    def test_the_tabs_spell_out_all_when_it_is_not_the_default(self):
        set_setting(self.user, "photos", "default_media_type", "photo")

        response = self.client.get("/photos")

        self.assertEqual(
            [(tab["url"], tab["active"]) for tab in response.context["type_tabs"]],
            [
                ("/photos?type=all", False),
                ("/photos", True),
                ("/photos?type=video", False),
            ],
        )

    @patch("workspace.photos.services.timeline.PAGE_SIZE", 1)
    def test_the_next_page_keeps_the_type_on_screen(self):
        set_setting(self.user, "photos", "default_media_type", "photo")

        response = self.client.get("/photos?type=all")

        match = re.search(r"timelineSentinel\('([^']+)'\)", response.content.decode())
        self.assertIn("type=all", json.loads(f'"{match[1]}"'))

    def test_an_empty_library_offers_the_import_whatever_the_default_type(self):
        set_setting(self.user, "photos", "default_media_type", "photo")
        self.photo.delete()
        self.video.delete()

        response = self.client.get("/photos")

        self.assertContains(response, "No photos yet")


class TileRenderingTests(PreferencesTestCase):
    def test_the_page_root_carries_the_tile_preferences(self):
        make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        set_setting(self.user, "photos", "tile_shape", "original")
        set_setting(self.user, "photos", "tile_badges", False)

        response = self.client.get("/photos")

        self.assertContains(response, 'data-tile-shape="original"')
        self.assertContains(response, 'data-tile-badges="hover"')

    def test_the_panel_reads_the_preferences_and_the_users_groups(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        set_setting(self.user, "photos", "import_by_date", True)

        response = self.client.get("/photos")

        prefs = json.loads(
            re.search(
                r'<script id="photos-prefs-data" type="application/json">(.*?)</script>',
                response.content.decode(),
            )[1]
        )
        self.assertIs(prefs["import_by_date"], True)
        self.assertContains(response, f'<option value="group:{family.pk}">Family')

    def test_a_tile_carries_its_aspect_ratio(self):
        wide = make_photo(
            self.user, "wide.jpg", _at(2024, 7, 14, 12), width=4000, height=3000
        )

        response = self.client.get("/photos")

        self.assertContains(response, "--tile-aspect: 1.3333")
        self.assertEqual(tile_aspect(wide), "1.3333")

    def test_the_aspect_ratio_is_clamped_and_square_without_a_size(self):
        panorama = make_photo(
            self.user, "pano.jpg", _at(2024, 7, 14, 12), width=9000, height=1000
        )
        sliver = make_photo(
            self.user, "long.png", _at(2024, 7, 14, 12), width=500, height=5000
        )
        unknown = make_photo(self.user, "scan.jpg", None)

        self.assertEqual(tile_aspect(panorama), "2.5000")
        self.assertEqual(tile_aspect(sliver), "0.5000")
        self.assertEqual(tile_aspect(unknown), "1")

    def test_only_video_tiles_start_a_hover_preview(self):
        photo = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        video = make_video(self.user, "surf.webm", _at(2024, 7, 13, 12))
        self.assertEqual(
            MediaItem.objects.get(file=video).media_type, MediaItem.MediaType.VIDEO
        )

        html = self.client.get("/photos").content.decode()

        def tile(file_obj):
            start = html.index(f'data-uuid="{file_obj.uuid}"')
            return html[start : html.index(">", start)]

        self.assertIn("startHoverPreview", tile(video))
        self.assertNotIn("startHoverPreview", tile(photo))
