from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from workspace.core.services.admin_dashboard import sensitive_backend_health_card
from workspace.files.models import File, FileShare
from workspace.files.sensitivity import is_sensitive, with_sensitivity
from workspace.files.services import FileService
from workspace.files.services.processors import get_processor
from workspace.files.services.sharing import share_file
from workspace.photos.models import MediaItem
from workspace.photos.services.album_cards import album_cards
from workspace.photos.services.albums import add_items, create_album
from workspace.photos.services.sensitive_photos import (
    check_photo,
    is_sensitive_candidate,
    mark_sensitive,
    pending_sensitive_qs,
)
from workspace.photos.services.sensitivity.fake import FakeSensitiveBackend
from workspace.photos.tasks import check_sensitive_photo
from workspace.users.services.settings import set_setting

from .images import jpeg_bytes, make_photo, make_video, upload

User = get_user_model()

detection_on = override_settings(
    PHOTOS_SENSITIVE_DETECTION_ENABLED=True,
    PHOTOS_SENSITIVE_BACKEND="fake",
    PHOTOS_SENSITIVE_THRESHOLD=None,
)

# What the fake detector makes of a picture: the share of red in its colour.
RED = (255, 0, 0)
BLUE = (0, 0, 255)
# Scores about 0.6: flagged at the fake's 0.5, in clear at 0.7.
PURPLE = (150, 0, 100)


def _at(year, month, day):
    return timezone.make_aware(timezone.datetime(year, month, day, 12))


def library_photo(owner, name, colour, **kwargs):
    """A photo of one *colour*, its MediaItem row written."""
    photo = upload(owner, name, jpeg_bytes(color=colour), **kwargs)
    MediaItem.objects.update_or_create(
        file=photo,
        defaults={"content_hash": photo.content_hash, "analyzed_at": timezone.now()},
    )
    return photo


def _item(file_obj):
    return MediaItem.objects.get(file=file_obj)


@detection_on
class CheckPhotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_an_explicit_photo_is_flagged_and_its_check_recorded(self):
        photo = library_photo(self.user, "red.jpg", RED)

        self.assertTrue(check_photo(photo))

        item = _item(photo)
        self.assertTrue(item.sensitive)
        self.assertAlmostEqual(item.sensitive_score, 1.0, places=2)
        self.assertEqual(item.sensitive_model, "fake")
        self.assertEqual(item.sensitive_hash, photo.content_hash)
        self.assertIsNone(item.sensitive_by_user)

    def test_a_harmless_photo_is_left_in_clear(self):
        photo = library_photo(self.user, "blue.jpg", BLUE)

        check_photo(photo)

        self.assertFalse(_item(photo).sensitive)

    @override_settings(PHOTOS_SENSITIVE_THRESHOLD=0.01)
    def test_the_threshold_setting_overrides_the_backends(self):
        photo = library_photo(self.user, "blue.jpg", (20, 0, 255))

        check_photo(photo)

        self.assertTrue(_item(photo).sensitive)

    def test_a_new_threshold_reuses_the_score_already_given(self):
        photo = library_photo(self.user, "purple.jpg", PURPLE)
        check_photo(photo)
        self.assertTrue(_item(photo).sensitive)
        score = _item(photo).sensitive_score

        with (
            override_settings(PHOTOS_SENSITIVE_THRESHOLD=0.9),
            patch.object(FakeSensitiveBackend, "score") as model,
        ):
            self.assertTrue(check_photo(photo))

        model.assert_not_called()
        self.assertFalse(_item(photo).sensitive)
        self.assertEqual(_item(photo).sensitive_score, score)

    def test_new_bytes_are_scored_again(self):
        photo = library_photo(self.user, "red.jpg", RED)
        check_photo(photo)
        photo.content.save("blue.jpg", ContentFile(jpeg_bytes(color=BLUE)), save=False)
        File.objects.filter(pk=photo.pk).update(content_hash="new")
        photo.content_hash = "new"

        check_photo(photo)

        self.assertFalse(_item(photo).sensitive)

    def test_a_choice_made_by_hand_wins_over_the_detector(self):
        red = library_photo(self.user, "red.jpg", RED)
        blue = library_photo(self.user, "blue.jpg", BLUE)
        mark_sensitive([red.pk], False)
        mark_sensitive([blue.pk], True)

        check_photo(red)
        check_photo(blue)

        self.assertFalse(_item(red).sensitive)
        self.assertTrue(_item(blue).sensitive)
        # Its verdict is still recorded, so the photo is not pending forever.
        self.assertAlmostEqual(_item(red).sensitive_score, 1.0, places=2)

    def test_an_undecodable_photo_is_recorded_without_a_score(self):
        photo = upload(self.user, "broken.jpg", jpeg_bytes())
        File.objects.filter(pk=photo.pk).update(type="image/jpeg")
        photo.content.save("broken.jpg", ContentFile(b"not a jpeg"), save=False)
        MediaItem.objects.create(file=photo, analyzed_at=timezone.now())

        self.assertTrue(check_photo(photo))

        item = _item(photo)
        self.assertIsNone(item.sensitive_score)
        self.assertFalse(item.sensitive)
        self.assertNotIn(photo, pending_sensitive_qs())

    def test_a_photo_replaced_while_it_was_read_is_left_to_the_next_pass(self):
        photo = library_photo(self.user, "red.jpg", RED)
        File.objects.filter(pk=photo.pk).update(content_hash="another")

        self.assertFalse(check_photo(photo))

        self.assertEqual(_item(photo).sensitive_model, "")

    def test_a_backend_failure_stores_nothing(self):
        photo = library_photo(self.user, "red.jpg", RED)

        with (
            patch.object(FakeSensitiveBackend, "score", side_effect=RuntimeError),
            self.assertLogs("workspace.photos.services.sensitive_photos", "ERROR"),
        ):
            self.assertFalse(check_photo(photo))

        self.assertIn(photo, pending_sensitive_qs())

    def test_a_video_is_not_checked(self):
        video = make_video(self.user, "clip.webm", _at(2024, 7, 14))

        self.assertFalse(is_sensitive_candidate(video))

    @override_settings(PHOTOS_SENSITIVE_DETECTION_ENABLED=False)
    def test_nothing_is_checked_with_detection_off(self):
        photo = library_photo(self.user, "red.jpg", RED)

        self.assertFalse(check_photo(photo))
        self.assertFalse(get_processor("sensitive").pending_files().exists())

    def test_the_task_checks_a_pending_photo(self):
        photo = library_photo(self.user, "red.jpg", RED)

        check_sensitive_photo(str(photo.pk))

        self.assertTrue(_item(photo).sensitive)


@detection_on
class PendingSensitiveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_a_photo_never_checked_is_pending_once_in_the_library(self):
        photo = upload(self.user, "red.jpg", jpeg_bytes(color=RED))
        MediaItem.objects.filter(file=photo).delete()
        self.assertNotIn(photo, pending_sensitive_qs())

        MediaItem.objects.create(
            file=photo, content_hash=photo.content_hash, analyzed_at=timezone.now()
        )

        self.assertIn(photo, pending_sensitive_qs())

    def test_a_checked_photo_is_pending_again_once_its_bytes_change(self):
        photo = library_photo(self.user, "red.jpg", RED)
        check_photo(photo)
        self.assertNotIn(photo, pending_sensitive_qs())

        File.objects.filter(pk=photo.pk).update(content_hash="other")

        self.assertIn(photo, pending_sensitive_qs())

    def test_a_photo_checked_by_another_detector_is_pending_again(self):
        photo = library_photo(self.user, "red.jpg", RED)
        check_photo(photo)

        MediaItem.objects.filter(file=photo).update(sensitive_model="older")

        self.assertIn(photo, pending_sensitive_qs())

    def test_a_photo_flagged_on_another_threshold_is_pending_again(self):
        photo = library_photo(self.user, "purple.jpg", PURPLE)
        check_photo(photo)

        with override_settings(PHOTOS_SENSITIVE_THRESHOLD=0.7):
            self.assertIn(photo, pending_sensitive_qs())
            check_photo(photo)
            self.assertNotIn(photo, pending_sensitive_qs())

    def test_a_photo_in_clear_is_pending_again_under_a_lower_threshold(self):
        photo = library_photo(self.user, "purple.jpg", PURPLE)
        with override_settings(PHOTOS_SENSITIVE_THRESHOLD=0.7):
            check_photo(photo)

        self.assertIn(photo, pending_sensitive_qs())

    def test_a_choice_made_by_hand_is_not_pending_on_a_new_threshold(self):
        photo = library_photo(self.user, "purple.jpg", PURPLE)
        check_photo(photo)
        mark_sensitive([photo.pk], True)

        with override_settings(PHOTOS_SENSITIVE_THRESHOLD=0.7):
            self.assertNotIn(photo, pending_sensitive_qs())

    def test_reanalyze_takes_every_library_photo(self):
        photo = library_photo(self.user, "red.jpg", RED)
        check_photo(photo)

        self.assertIn(photo, pending_sensitive_qs(reanalyze=True))


class MarkSensitiveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_marks_the_library_items_and_skips_other_files(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14))
        note = FileService.create_file(
            owner=self.user, name="n.md", content=ContentFile(b"# hi", name="n.md")
        )

        self.assertEqual(mark_sensitive([photo.pk, note.pk], True), 1)

        item = _item(photo)
        self.assertTrue(item.sensitive)
        self.assertTrue(item.sensitive_by_user)

    def test_unmarking_records_the_choice(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), sensitive=True)

        mark_sensitive([photo.pk], False)

        item = _item(photo)
        self.assertFalse(item.sensitive)
        self.assertIs(item.sensitive_by_user, False)

    def test_files_reads_the_flag_through_its_hook(self):
        flagged = make_photo(self.user, "a.jpg", _at(2024, 7, 14), sensitive=True)
        clear = make_photo(self.user, "b.jpg", _at(2024, 7, 14))
        note = FileService.create_file(
            owner=self.user, name="n.md", content=ContentFile(b"# hi", name="n.md")
        )

        flags = dict(
            with_sensitivity(File.objects.filter(owner=self.user)).values_list(
                "uuid", "is_sensitive"
            )
        )

        self.assertIs(flags[flagged.uuid], True)
        self.assertIs(flags[clear.uuid], False)
        self.assertIsNone(flags[note.uuid])
        self.assertIs(is_sensitive(flagged), True)


class AlbumCoverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_a_sensitive_cover_says_so(self):
        album = create_album(self.user, "Trip")
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), sensitive=True)
        add_items(album, [photo], added_by=self.user)

        (card,) = album_cards(self.user, [album])

        self.assertTrue(card.cover_sensitive)

    def test_a_cover_in_clear_says_so(self):
        album = create_album(self.user, "Trip")
        add_items(
            album,
            [make_photo(self.user, "a.jpg", _at(2024, 7, 14))],
            added_by=self.user,
        )

        (card,) = album_cards(self.user, [album])

        self.assertFalse(card.cover_sensitive)

    def test_the_album_list_exposes_it(self):
        album = create_album(self.user, "Trip")
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), sensitive=True)
        add_items(album, [photo], added_by=self.user)
        self.client.force_login(self.user)

        (data,) = self.client.get("/api/v1/photos/albums").json()

        self.assertIs(data["cover_sensitive"], True)


class SensitiveFilesApiTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.photo = make_photo(self.alice, "a.jpg", _at(2024, 7, 14))
        self.client.force_login(self.alice)

    def _post(self, data):
        return self.client.post(
            "/api/v1/files/sensitive", data, content_type="application/json"
        )

    def test_the_owner_marks_and_unmarks_a_photo(self):
        response = self._post({"uuids": [str(self.photo.uuid)], "sensitive": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"marked": 1, "sensitive": True})
        self.assertTrue(_item(self.photo).sensitive)

        self._post({"uuids": [str(self.photo.uuid)], "sensitive": False})

        self.assertFalse(_item(self.photo).sensitive)

    def test_a_group_member_marks_a_group_photo(self):
        group = Group.objects.create(name="Family")
        group.user_set.add(self.alice, self.bob)
        folder = FileService.create_folder(owner=self.alice, name="Family", group=group)
        photo = make_photo(self.alice, "g.jpg", _at(2024, 7, 14), parent=folder)
        self.client.force_login(self.bob)

        response = self._post({"uuids": [str(photo.uuid)], "sensitive": True})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(_item(photo).sensitive)

    def test_a_photo_shared_with_the_caller_is_refused_and_nothing_is_marked(self):
        bobs = make_photo(self.bob, "b.jpg", _at(2024, 7, 14))
        share_file(
            bobs,
            target_user=self.alice,
            permission=FileShare.Permission.READ_WRITE,
            acting_user=self.bob,
        )

        response = self._post(
            {"uuids": [str(self.photo.uuid), str(bobs.uuid)], "sensitive": True}
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(_item(self.photo).sensitive)
        self.assertFalse(_item(bobs).sensitive)

    def test_a_file_outside_the_library_is_refused(self):
        note = FileService.create_file(
            owner=self.alice, name="n.md", content=ContentFile(b"# hi", name="n.md")
        )

        response = self._post({"uuids": [str(note.uuid)], "sensitive": True})

        self.assertEqual(response.status_code, 403)

    def test_someone_elses_photo_is_not_found(self):
        bobs = make_photo(self.bob, "b.jpg", _at(2024, 7, 14))

        response = self._post({"uuids": [str(bobs.uuid)], "sensitive": True})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(_item(bobs).sensitive)

    def test_a_malformed_body_is_refused(self):
        for body in (
            {"uuids": [str(self.photo.uuid)]},
            {"uuids": [str(self.photo.uuid)], "sensitive": "yes"},
            {"uuids": ["nope"], "sensitive": True},
            {"uuids": [], "sensitive": True},
            [str(self.photo.uuid)],
        ):
            with self.subTest(body=body):
                self.assertEqual(self._post(body).status_code, 400)

    def test_the_actions_endpoint_offers_the_toggle_with_its_state(self):
        mark_sensitive([self.photo.pk], True)

        response = self.client.post(
            "/api/v1/files/actions",
            {"uuids": [str(self.photo.uuid)]},
            content_type="application/json",
        ).json()

        keys = response["files"][str(self.photo.uuid)]
        (toggle,) = [
            response["actions"][key]
            for key in keys
            if response["actions"][key]["id"] == "toggle_sensitive"
        ]
        self.assertEqual(toggle["label"], "Unmark as sensitive")
        self.assertEqual(toggle["state"], {"is_sensitive": True})


class SensitivePreviewTests(TestCase):
    """Where a sensitive photo's preview is blurred, and the viewer covered."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), sensitive=True)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_the_timeline_tile_carries_the_flag(self):
        html = self.client.get("/photos").content.decode()

        self.assertIn('data-sensitive="1"', html)

    def test_the_page_blurs_by_default(self):
        html = self.client.get("/photos").content.decode()

        self.assertIn("data-blur-sensitive", html.split("<head>")[0])

    def test_the_page_does_not_blur_once_the_user_turned_it_off(self):
        response = self.client.put(
            "/api/v1/settings/core/blur_sensitive",
            {"value": False},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        html = self.client.get("/photos").content.decode()

        self.assertNotIn("data-blur-sensitive", html.split("<head>")[0])

    def test_the_setting_must_be_a_boolean(self):
        response = self.client.put(
            "/api/v1/settings/core/blur_sensitive",
            {"value": "off"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_the_reveal_options_are_off_by_default(self):
        root = self.client.get("/photos").content.decode().split("<head>")[0]

        self.assertNotIn("data-reveal-sensitive-on-hover", root)
        self.assertNotIn("data-reveal-sensitive-in-viewer", root)

    def test_the_page_carries_the_reveal_options_the_user_turned_on(self):
        for key in ("reveal_sensitive_on_hover", "reveal_sensitive_in_viewer"):
            response = self.client.put(
                f"/api/v1/settings/core/{key}",
                {"value": True},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        root = self.client.get("/photos").content.decode().split("<head>")[0]

        self.assertIn("data-blur-sensitive", root)
        self.assertIn("data-reveal-sensitive-on-hover", root)
        self.assertIn("data-reveal-sensitive-in-viewer", root)

    def test_the_reveal_options_must_be_booleans(self):
        for key in ("reveal_sensitive_on_hover", "reveal_sensitive_in_viewer"):
            response = self.client.put(
                f"/api/v1/settings/core/{key}",
                {"value": "on"},
                content_type="application/json",
            )

            self.assertEqual(response.status_code, 400, key)

    def test_the_files_viewer_is_covered(self):
        html = self.client.get(f"/files/view/{self.photo.uuid}").content.decode()

        self.assertIn("sensitive-cover", html)
        self.assertIn("sensitive-veiled", html)

    def test_the_files_viewer_of_a_photo_in_clear_is_not(self):
        clear = make_photo(self.user, "b.jpg", _at(2024, 7, 14))

        html = self.client.get(f"/files/view/{clear.uuid}").content.decode()

        self.assertNotIn("sensitive-cover", html)

    def test_the_files_mosaic_blurs_it(self):
        set_setting(self.user, "files", "preferences", {"defaultViewMode": "mosaic"})

        html = self.client.get("/files").content.decode()

        self.assertIn('data-sensitive="1"', html)


class SensitiveHealthCardTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")

    def tearDown(self):
        cache.clear()

    @override_settings(PHOTOS_SENSITIVE_DETECTION_ENABLED=False)
    def test_no_card_when_detection_is_off(self):
        self.assertIsNone(sensitive_backend_health_card(self.request))

    @override_settings(
        PHOTOS_SENSITIVE_DETECTION_ENABLED=True, PHOTOS_SENSITIVE_BACKEND="nsfw"
    )
    def test_a_misconfigured_backend_shows_as_an_error(self):
        card = sensitive_backend_health_card(self.request)

        self.assertEqual(card["tone"], "danger")
        self.assertIn("unknown sensitive-content backend 'nsfw'", card["value"])

    @detection_on
    def test_a_working_backend_shows_as_healthy(self):
        self.assertEqual(sensitive_backend_health_card(self.request)["tone"], "success")
