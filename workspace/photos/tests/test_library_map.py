from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from workspace.files.models import File
from workspace.photos.models import HiddenFile, MediaItem
from workspace.photos.queries import library_files
from workspace.photos.services.library_map import (
    Area,
    in_area,
    in_period,
    map_points,
    month_index,
    month_token,
    parse_area,
    parse_month,
)
from workspace.photos.services.location_map import location_map
from workspace.users.services.settings import set_setting

from .images import make_photo, make_video

User = get_user_model()

TILE_URL = "https://tiles.example.test/{z}/{x}/{y}.png"
ATTRIBUTION = "&copy; Example"
CONFIGURED = override_settings(
    PHOTOS_MAP_TILE_URL=TILE_URL,
    PHOTOS_MAP_TILE_URL_DARK="",
    PHOTOS_MAP_TILE_ATTRIBUTION=ATTRIBUTION,
)
UNCONFIGURED = override_settings(
    PHOTOS_MAP_TILE_URL="",
    PHOTOS_MAP_TILE_URL_DARK="",
    PHOTOS_MAP_TILE_ATTRIBUTION="",
)
PARIS = ZoneInfo("Europe/Paris")


class AreaTests(SimpleTestCase):
    def test_reads_west_south_east_north(self):
        self.assertEqual(parse_area("2.2,48.8,2.4,48.9"), Area(2.2, 48.8, 2.4, 48.9))

    def test_wraps_longitudes_past_the_antimeridian(self):
        # Leaflet keeps counting past 180 when the map is panned east.
        self.assertEqual(parse_area("170,-10,190,10"), Area(170, -10, -170, 10))

    def test_keeps_the_antimeridian_itself(self):
        self.assertEqual(parse_area("-180,-10,180,10"), Area(-180, -10, 180, 10))

    def test_a_world_or_more_takes_every_longitude(self):
        self.assertEqual(parse_area("-300,-80,300,80"), Area(-180, -80, 180, 80))

    def test_refuses_anything_else(self):
        for raw in (
            "",
            "1,2,3",
            "1,2,3,4,5",
            "a,b,c,d",
            "nan,0,1,1",
            "0,0,inf,1",
            "0,10,1,5",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_area(raw)


class MonthTests(SimpleTestCase):
    def test_round_trip(self):
        self.assertEqual(month_token(parse_month("2024-07")), "2024-07")
        self.assertEqual(parse_month("2024-01") + 11, parse_month("2024-12"))

    def test_counted_in_the_users_timezone(self):
        # 23:30 UTC on 31 July is already August in Paris.
        moment = datetime(2024, 7, 31, 23, 30, tzinfo=UTC)

        self.assertEqual(month_token(month_index(moment, UTC)), "2024-07")
        self.assertEqual(month_token(month_index(moment, PARIS)), "2024-08")

    def test_refuses_anything_else(self):
        for raw in (
            "",
            "2024",
            "2024-7",
            "2024-13",
            "2024-00",
            "24-07",
            "9999-12",
            "abcd-ef",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_month(raw)


class LibraryMapQueryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.paris = make_photo(
            self.user,
            "paris.jpg",
            datetime(2024, 7, 31, 23, 30, tzinfo=UTC),
            latitude=48.858437,
            longitude=2.294481,
        )
        self.fiji = make_photo(
            self.user,
            "fiji.jpg",
            datetime(2023, 1, 5, tzinfo=UTC),
            latitude=-17.7,
            longitude=178.1,
        )
        self.samoa = make_photo(
            self.user, "samoa.jpg", None, latitude=-13.8, longitude=-171.7
        )
        self.nowhere = make_photo(
            self.user, "nowhere.jpg", datetime(2024, 1, 1, tzinfo=UTC)
        )

    def _names(self, files):
        return set(files.values_list("name", flat=True))

    def test_points_are_the_located_photos_newest_first_undated_last(self):
        points = map_points(library_files(self.user), UTC)

        self.assertEqual(
            [p[0] for p in points],
            [str(self.paris.uuid), str(self.fiji.uuid), str(self.samoa.uuid)],
        )
        paris = points[0]
        self.assertEqual((paris[1], paris[2]), (48.85844, 2.29448))
        self.assertEqual(month_token(paris[3]), "2024-07")
        self.assertIsNone(points[2][3])

    def test_point_months_follow_the_timezone(self):
        points = map_points(library_files(self.user), PARIS)

        self.assertEqual(month_token(points[0][3]), "2024-08")

    def test_point_flags_a_video_and_its_sensitivity(self):
        video = make_video(
            self.user,
            "clip.webm",
            datetime(2025, 1, 1, tzinfo=UTC),
            latitude=1.0,
            longitude=1.0,
        )
        MediaItem.objects.filter(file=video).update(sensitive_level=3)

        point = map_points(library_files(self.user), UTC)[0]

        self.assertEqual(point[0], str(video.uuid))
        self.assertEqual((point[4], point[5]), (1, 3))

    def test_point_names_the_thumbnail_version(self):
        File.objects.filter(pk=self.paris.pk).update(has_thumbnail=False)
        File.objects.filter(pk=self.fiji.pk).update(has_thumbnail=True, image_edit=None)
        File.objects.filter(pk=self.samoa.pk).update(
            has_thumbnail=True, image_edit={"rotation": 90}
        )

        thumbnails = {p[0]: p[6] for p in map_points(library_files(self.user), UTC)}

        self.assertIsNone(thumbnails[str(self.paris.uuid)])
        self.assertEqual(thumbnails[str(self.fiji.uuid)], "")
        self.assertTrue(thumbnails[str(self.samoa.uuid)])

    def test_in_area(self):
        area = parse_area("2,48,3,49")

        self.assertEqual(
            self._names(in_area(library_files(self.user), area)), {"paris.jpg"}
        )

    def test_in_area_across_the_antimeridian(self):
        area = parse_area("170,-20,190,-10")

        self.assertEqual(
            self._names(in_area(library_files(self.user), area)),
            {"fiji.jpg", "samoa.jpg"},
        )

    def test_in_period_counts_months_in_the_timezone_and_drops_undated(self):
        july = parse_month("2024-07")
        files = library_files(self.user)

        self.assertEqual(self._names(in_period(files, july, july, UTC)), {"paris.jpg"})
        self.assertEqual(self._names(in_period(files, july, july, PARIS)), set())
        self.assertEqual(
            self._names(in_period(files, parse_month("2023-01"), july + 1, PARIS)),
            {"paris.jpg", "fiji.jpg", "nowhere.jpg"},
        )


@UNCONFIGURED
class LibraryMapApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.other = User.objects.create_user(username="bob", password="p")
        self.client.force_login(self.user)
        self.photo = make_photo(
            self.user,
            "paris.jpg",
            datetime(2024, 7, 1, tzinfo=UTC),
            latitude=48.85,
            longitude=2.29,
        )
        self.video = make_video(
            self.user,
            "clip.webm",
            datetime(2024, 6, 1, tzinfo=UTC),
            latitude=45.0,
            longitude=5.0,
        )
        make_photo(self.user, "nowhere.jpg", datetime(2024, 1, 1, tzinfo=UTC))
        make_photo(
            self.other,
            "bobs.jpg",
            datetime(2024, 7, 2, tzinfo=UTC),
            latitude=40.0,
            longitude=-3.7,
        )

    def tearDown(self):
        cache.clear()

    def _uuids(self, **params):
        response = self.client.get(reverse("photo-library-map"), params)
        self.assertEqual(response.status_code, 200)
        return [point[0] for point in response.json()["points"]]

    def test_404_without_a_tile_server(self):
        response = self.client.get(reverse("photo-library-map"))

        self.assertEqual(response.status_code, 404)

    @CONFIGURED
    def test_lists_the_located_photos_and_videos_of_the_library(self):
        self.assertEqual(self._uuids(), [str(self.photo.uuid), str(self.video.uuid)])

    @CONFIGURED
    def test_narrows_to_a_media_type(self):
        self.assertEqual(self._uuids(type="video"), [str(self.video.uuid)])
        self.assertEqual(self._uuids(type="photo"), [str(self.photo.uuid)])

    @CONFIGURED
    def test_leaves_out_what_the_user_hid(self):
        HiddenFile.objects.create(owner=self.user, file=self.photo)

        self.assertEqual(self._uuids(), [str(self.video.uuid)])

    @CONFIGURED
    def test_never_lists_another_users_photos(self):
        self.assertEqual(
            self._uuids(scope="all"), [str(self.photo.uuid), str(self.video.uuid)]
        )

    @CONFIGURED
    def test_404_on_a_group_the_user_is_not_in(self):
        response = self.client.get(reverse("photo-library-map"), {"scope": "group:999"})

        self.assertEqual(response.status_code, 404)

    @CONFIGURED
    def test_400_on_an_unknown_media_type(self):
        response = self.client.get(reverse("photo-library-map"), {"type": "audio"})

        self.assertEqual(response.status_code, 400)

    @CONFIGURED
    def test_refuses_an_anonymous_caller(self):
        self.client.logout()

        response = self.client.get(reverse("photo-library-map"))

        self.assertIn(response.status_code, (401, 403))


@UNCONFIGURED
class LibraryMapPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        make_photo(
            self.user,
            "paris.jpg",
            datetime(2024, 7, 1, tzinfo=UTC),
            latitude=48.85,
            longitude=2.29,
        )
        make_photo(self.user, "nowhere.jpg", datetime(2024, 1, 1, tzinfo=UTC))

    def tearDown(self):
        cache.clear()

    def _page(self, **params):
        response = self.client.get(reverse("photos_ui:map"), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _config(self, **params):
        response = self.client.get(reverse("photos_ui:map"), params)
        self.assertEqual(response.status_code, 200)
        return response.context["map_config"]

    def test_404_without_a_tile_server(self):
        self.assertEqual(self.client.get(reverse("photos_ui:map")).status_code, 404)

    def test_no_map_entry_in_the_sidebar_without_a_tile_server(self):
        html = self.client.get(reverse("photos_ui:index")).content.decode()

        self.assertNotIn(reverse("photos_ui:map"), html)

    @CONFIGURED
    def test_map_entry_in_the_sidebar_carries_the_library(self):
        html = self.client.get(
            reverse("photos_ui:index"), {"scope": "all"}
        ).content.decode()

        self.assertIn(f'href="{reverse("photos_ui:map")}?scope=all"', html)

    @CONFIGURED
    def test_map_entry_is_in_a_swapped_sidebar_too(self):
        response = self.client.get(
            reverse("photos_ui:index"), headers={"X-Alpine-Request": "true"}
        )

        self.assertIn(f'href="{reverse("photos_ui:map")}"', response.content.decode())

    @CONFIGURED
    def test_offered_until_the_user_turns_the_map_on(self):
        html = self._page()

        self.assertIn("data-library-map-offer", html)
        self.assertIn("This library holds 1 photo with a location.", html)
        self.assertIn("loaded from tiles.example.test", html)
        self.assertNotIn("library-map-data", html)

    @CONFIGURED
    def test_drawn_once_turned_on(self):
        set_setting(self.user, "photos", "show_map", True)

        config = self._config()

        self.assertEqual(config["tileUrl"], TILE_URL)
        self.assertEqual(config["attribution"], ATTRIBUTION)
        self.assertIsNone(config["view"])
        self.assertIsNone(config["period"])
        points = urlsplit(config["pointsUrl"])
        self.assertEqual(points.path, reverse("photo-library-map"))
        self.assertEqual(parse_qs(points.query), {"scope": ["mine"], "type": ["all"]})
        self.assertEqual(
            urlsplit(config["photosUrl"]).path, reverse("photos_ui:map_photos")
        )

    @CONFIGURED
    def test_opens_on_the_view_and_period_of_the_url(self):
        set_setting(self.user, "photos", "show_map", True)

        config = self._config(
            lat="48.85", lng="2.29", z="12", **{"from": "2024-01", "to": "2024-07"}
        )

        self.assertEqual(config["view"], {"lat": 48.85, "lng": 2.29, "zoom": 12.0})
        self.assertEqual(config["period"], {"from": "2024-01", "to": "2024-07"})

    @CONFIGURED
    def test_drops_a_malformed_view_or_period(self):
        set_setting(self.user, "photos", "show_map", True)

        for params in (
            {"lat": "91", "lng": "0", "z": "3"},
            {"lat": "nan", "lng": "0", "z": "3"},
            {"lat": "1", "lng": "0"},
            {"lat": "1", "lng": "0", "z": "40"},
            {"from": "2024-07", "to": "2024-01"},
            {"from": "2024-07"},
            {"from": "July", "to": "2024-01"},
        ):
            with self.subTest(params=params):
                config = self._config(**params)
                self.assertIsNone(config["view"])
                self.assertIsNone(config["period"])

    @CONFIGURED
    def test_404_on_a_group_the_user_is_not_in(self):
        response = self.client.get(reverse("photos_ui:map"), {"scope": "group:999"})

        self.assertEqual(response.status_code, 404)

    @CONFIGURED
    def test_library_tabs_stay_on_the_map(self):
        html = self._page(scope="all")

        self.assertIn(f'href="{reverse("photos_ui:map")}"', html)
        self.assertIn(f'href="{reverse("photos_ui:map")}?scope=all"', html)


@UNCONFIGURED
class LibraryMapPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        self.louvre = make_photo(
            self.user,
            "louvre.jpg",
            datetime(2024, 7, 1, tzinfo=UTC),
            latitude=48.861,
            longitude=2.336,
        )
        self.tower = make_photo(
            self.user,
            "tower.jpg",
            datetime(2023, 3, 1, tzinfo=UTC),
            latitude=48.858,
            longitude=2.294,
        )
        self.lyon = make_photo(
            self.user,
            "lyon.jpg",
            datetime(2024, 5, 1, tzinfo=UTC),
            latitude=45.76,
            longitude=4.83,
        )

    def tearDown(self):
        cache.clear()

    def _get(self, **params):
        return self.client.get(reverse("photos_ui:map_photos"), params)

    def _grid(self, **params):
        response = self._get(**params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_404_without_a_tile_server(self):
        self.assertEqual(self._get(bbox="2,48,3,49").status_code, 404)

    @CONFIGURED
    def test_lists_the_photos_of_the_area(self):
        html = self._grid(bbox="2,48,3,49")

        self.assertIn('id="map-photos"', html)
        self.assertIn(str(self.louvre.uuid), html)
        self.assertIn(str(self.tower.uuid), html)
        self.assertNotIn(str(self.lyon.uuid), html)
        self.assertIn("2 photos", html)

    @CONFIGURED
    def test_narrows_to_the_period(self):
        html = self._grid(bbox="2,48,3,49", **{"from": "2024-01", "to": "2024-12"})

        self.assertIn(str(self.louvre.uuid), html)
        self.assertNotIn(str(self.tower.uuid), html)

    @CONFIGURED
    def test_says_when_the_area_is_empty(self):
        html = self._grid(bbox="-10,-10,-9,-9")

        self.assertIn("Nothing was taken in this area", html)

    @CONFIGURED
    def test_400_on_a_malformed_area_period_or_cursor(self):
        for params in (
            {},
            {"bbox": "2,48,3"},
            {"bbox": "2,48,3,49", "from": "2024-13", "to": "2024-12"},
            {"bbox": "2,48,3,49", "cursor": "garbage"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self._get(**params).status_code, 400)

    @CONFIGURED
    @patch("workspace.photos.services.timeline.PAGE_SIZE", 1)
    def test_pages_keep_the_area_and_period(self):
        response = self._get(bbox="2,48,3,49", **{"from": "2023-01", "to": "2024-12"})
        next_url = response.context["next_url"]

        query = parse_qs(urlsplit(next_url).query)
        self.assertEqual(urlsplit(next_url).path, reverse("photos_ui:map_photos"))
        self.assertEqual(query["bbox"], ["2.0,48.0,3.0,49.0"])
        self.assertEqual((query["from"], query["to"]), (["2023-01"], ["2024-12"]))
        page = self._grid(**{k: v[0] for k, v in query.items()})
        self.assertIn(str(self.tower.uuid), page)
        self.assertNotIn('id="map-photos"', page)


@UNCONFIGURED
class PanelLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    @CONFIGURED
    def test_the_photos_map_links_to_the_map_page_around_it(self):
        set_setting(self.user, "photos", "show_map", True)

        drawn = location_map(self.user, MediaItem(latitude=48.8584, longitude=2.2945))

        url = urlsplit(drawn.library_url)
        self.assertEqual(url.path, reverse("photos_ui:map"))
        self.assertEqual(
            parse_qs(url.query),
            {"scope": ["all"], "lat": ["48.8584"], "lng": ["2.2945"], "z": ["16"]},
        )
