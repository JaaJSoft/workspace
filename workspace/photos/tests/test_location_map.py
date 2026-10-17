from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.photos.models import MediaItem
from workspace.photos.services.location_map import location_map, tile_server
from workspace.users.services.settings import set_setting

from .images import make_photo

User = get_user_model()

TILE_URL = "https://tiles.example.test/{z}/{x}/{y}.png"
ATTRIBUTION = '&copy; <a href="https://example.test/copyright">Example</a>\'s maps'
CONFIGURED = override_settings(
    PHOTOS_MAP_TILE_URL=TILE_URL, PHOTOS_MAP_TILE_ATTRIBUTION=ATTRIBUTION
)


class LocationMapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def _item(self, **fields):
        return MediaItem(latitude=48.8584, longitude=2.2945, **fields)

    def test_no_map_without_a_tile_server(self):
        set_setting(self.user, "photos", "show_map", True)

        self.assertIsNone(location_map(self.user, self._item()))

    @override_settings(PHOTOS_MAP_TILE_URL=TILE_URL, PHOTOS_MAP_TILE_ATTRIBUTION="")
    def test_no_map_without_an_attribution(self):
        set_setting(self.user, "photos", "show_map", True)

        self.assertIsNone(location_map(self.user, self._item()))

    @CONFIGURED
    def test_no_map_without_a_position(self):
        set_setting(self.user, "photos", "show_map", True)

        self.assertIsNone(location_map(self.user, MediaItem(latitude=48.8584)))

    @CONFIGURED
    def test_offered_but_not_drawn_until_the_user_turns_it_on(self):
        offer = location_map(self.user, self._item())

        self.assertFalse(offer.enabled)
        self.assertEqual(offer.server, "tiles.example.test")
        self.assertEqual((offer.tile_url, offer.latitude), ("", None))

    @CONFIGURED
    def test_drawn_once_turned_on(self):
        set_setting(self.user, "photos", "show_map", True)

        drawn = location_map(self.user, self._item())

        self.assertTrue(drawn.enabled)
        self.assertEqual(drawn.tile_url, TILE_URL)
        self.assertEqual(drawn.tile_url_dark, "")
        self.assertEqual(drawn.attribution, ATTRIBUTION)
        self.assertEqual((drawn.latitude, drawn.longitude), (48.8584, 2.2945))

    @override_settings(
        PHOTOS_MAP_TILE_URL="https://{s}.tile.example.test/{z}/{x}/{y}.png"
    )
    def test_server_named_without_the_subdomain_placeholder(self):
        self.assertEqual(tile_server(), "tile.example.test")

    @CONFIGURED
    @override_settings(
        PHOTOS_MAP_TILE_URL_DARK="https://dark.example.test/{z}/{x}/{y}.png"
    )
    def test_a_dark_style_on_another_host_is_named_too(self):
        self.assertEqual(tile_server(), "tiles.example.test and dark.example.test")

    @CONFIGURED
    @override_settings(
        PHOTOS_MAP_TILE_URL_DARK="https://tiles.example.test/dark/{z}/{x}/{y}.png"
    )
    def test_a_dark_style_on_the_same_host_is_named_once(self):
        self.assertEqual(tile_server(), "tiles.example.test")


class LocationMapPanelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        self.photo = make_photo(
            self.user,
            "eiffel.jpg",
            datetime(2024, 7, 14, tzinfo=UTC),
            latitude=48.8584,
            longitude=2.2945,
        )

    def tearDown(self):
        cache.clear()

    def _panel(self, file_obj=None):
        response = self.client.get(
            reverse(
                "files_ui:properties", kwargs={"uuid": (file_obj or self.photo).uuid}
            )
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_no_map_and_no_offer_without_a_tile_server(self):
        set_setting(self.user, "photos", "show_map", True)

        html = self._panel()

        self.assertIn("48.85840, 2.29450", html)
        self.assertNotIn("data-photo-map", html)
        self.assertNotIn("Show on a map", html)

    @CONFIGURED
    def test_offer_names_the_server_and_sends_it_nothing(self):
        html = self._panel()

        self.assertIn("data-photo-map-offer", html)
        self.assertIn("Show on a map", html)
        self.assertIn("loaded from tiles.example.test", html)
        self.assertNotIn("photoMap(", html)
        self.assertNotIn("tiles.example.test/{z}", html)

    @CONFIGURED
    def test_map_with_the_configured_server_and_attribution(self):
        set_setting(self.user, "photos", "show_map", True)

        html = self._panel()

        self.assertIn("photoMap({", html)
        self.assertIn("tileUrl: 'https://tiles.example.test/{z}/{x}/{y}.png'", html)
        # escapejs keeps the attribution's quote inside its JS string.
        self.assertIn("Example\\u003C/a\\u003E\\u0027s maps", html)
        self.assertIn("latitude: 48.8584, longitude: 2.2945, zoom: 16", html)
        self.assertIn("tileUrlDark: ''", html)
        self.assertNotIn("data-photo-map-offer", html)

    @CONFIGURED
    @override_settings(
        PHOTOS_MAP_TILE_URL_DARK="https://dark.example.test/{z}/{x}/{y}.png"
    )
    def test_map_with_a_dark_style(self):
        set_setting(self.user, "photos", "show_map", True)

        html = self._panel()

        self.assertIn("tileUrlDark: 'https://dark.example.test/{z}/{x}/{y}.png'", html)

    @CONFIGURED
    @override_settings(LANGUAGE_CODE="fr", USE_THOUSAND_SEPARATOR=True)
    def test_coordinates_stay_js_numbers_under_a_comma_locale(self):
        set_setting(self.user, "photos", "show_map", True)

        self.assertIn("latitude: 48.8584, longitude: 2.2945", self._panel())

    @CONFIGURED
    def test_nothing_for_a_photo_without_a_position(self):
        set_setting(self.user, "photos", "show_map", True)
        f = make_photo(self.user, "indoors.jpg", None, iso=100)

        html = self._panel(f)

        self.assertIn('data-properties-section="photo"', html)
        self.assertNotIn("data-photo-map", html)


class LocationMapPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_files_and_photos_pages_load_the_map(self):
        for url in (reverse("files_ui:index"), reverse("photos_ui:index")):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()

                self.assertIn("photos/ui/js/vendor/leaflet/leaflet.js", html)
                self.assertIn("photos/ui/js/photo_map.js", html)
                self.assertIn("photos/ui/css/vendor/leaflet/leaflet.css", html)

    def test_preference_offered_only_with_a_tile_server(self):
        url = reverse("photos_ui:index")

        self.assertNotIn("photos-pref-show-map", self.client.get(url).content.decode())
        with CONFIGURED:
            html = self.client.get(url).content.decode()
        self.assertIn('data-testid="photos-pref-show-map"', html)
        self.assertIn("loaded from tiles.example.test", html)

    def test_settings_api_takes_a_boolean_only(self):
        url = "/api/v1/settings/photos/show_map"

        refused = self.client.put(
            url, {"value": "yes"}, content_type="application/json"
        )
        accepted = self.client.put(
            url, {"value": True}, content_type="application/json"
        )

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
