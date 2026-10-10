import json
import re
from datetime import UTC, datetime
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileShare, FileTag, Tag
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.photos.models import HiddenFile, MediaItem
from workspace.photos.queries import (
    ALL,
    MINE,
    album_files,
    hidden_folders,
    library_files,
    library_groups,
    library_tags,
    unanalyzed_count,
)
from workspace.photos.search import search_photos
from workspace.photos.services.albums import create_album
from workspace.photos.services.hidden import hide_files, unhide_files
from workspace.users.services.settings import set_setting

from .images import make_photo, upload

User = get_user_model()

HIDE_URL = "/api/v1/photos/hidden"
UNHIDE_URL = "/api/v1/photos/hidden/remove"
FOLDERS_URL = "/api/v1/photos/hidden/folders"


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def _next_url(response):
    """The url the page's scroll sentinel will fetch, or None on the last page."""
    match = re.search(r"timelineSentinel\('([^']+)'\)", response.content.decode())
    # The url is written through |escapejs, as a JS string literal.
    return match and json.loads(f'"{match[1]}"')


class HiddenPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14))
        self.dog = make_photo(self.user, "dog.jpg", _at(2024, 7, 15))

    def test_a_hidden_photo_leaves_the_library(self):
        hide_files(self.user, [self.beach])

        self.assertEqual(list(library_files(self.user)), [self.dog])
        self.assertEqual(list(library_files(self.user, ALL)), [self.dog])

    def test_hidden_asks_for_the_hidden_photos_alone(self):
        hide_files(self.user, [self.beach])

        self.assertEqual(list(library_files(self.user, hidden=True)), [self.beach])
        self.assertEqual(list(library_files(self.user, ALL, hidden=True)), [self.beach])

    def test_nothing_hidden_is_an_empty_hidden_view(self):
        self.assertEqual(list(library_files(self.user, hidden=True)), [])

    def test_unhiding_brings_the_photo_back(self):
        hide_files(self.user, [self.beach])

        self.assertEqual(unhide_files(self.user, [self.beach.uuid]), 1)
        self.assertEqual(set(library_files(self.user)), {self.beach, self.dog})

    def test_hiding_twice_counts_once(self):
        self.assertEqual(hide_files(self.user, [self.beach]), 1)
        self.assertEqual(hide_files(self.user, [self.beach, self.dog]), 1)
        self.assertEqual(HiddenFile.objects.filter(owner=self.user).count(), 2)

    def test_hidden_photos_leave_albums_tags_and_search(self):
        album = create_album(self.user, "Summer", files=[self.beach, self.dog])
        summer = Tag.objects.create(owner=self.user, name="Summer")
        FileTag.objects.create(file=self.beach, tag=summer)

        hide_files(self.user, [self.beach])

        self.assertEqual(list(album_files(self.user, album)), [self.dog])
        self.assertEqual(list(library_tags(self.user)), [])
        self.assertEqual(search_photos("beach", self.user, 10), [])

    def test_an_unanalyzed_hidden_photo_is_not_announced(self):
        fresh = upload(self.user, "fresh.jpg")
        self.assertEqual(unanalyzed_count(self.user), 1)

        hide_files(self.user, [fresh])

        self.assertEqual(unanalyzed_count(self.user), 0)

    def test_what_one_user_hides_stays_in_everyone_elses_library(self):
        bob = User.objects.create_user(username="bob", password="p")
        share_file(
            self.beach,
            target_user=bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.user,
        )

        hide_files(bob, [self.beach])

        self.assertEqual(set(library_files(self.user)), {self.beach, self.dog})
        self.assertEqual(list(library_files(bob, ALL)), [])


class HiddenFoldersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.trips = FileService.create_folder(owner=self.user, name="Trips")
        self.rome = FileService.create_folder(
            owner=self.user, name="Rome", parent=self.trips
        )
        self.colosseum = make_photo(
            self.user, "colosseum.jpg", _at(2024, 5, 1), parent=self.rome
        )
        self.boat = make_photo(
            self.user, "boat.jpg", _at(2024, 5, 2), parent=self.trips
        )
        self.home = make_photo(self.user, "home.jpg", _at(2024, 5, 3))

    def test_a_hidden_folder_hides_everything_under_it(self):
        hide_files(self.user, [self.trips])

        self.assertEqual(list(library_files(self.user)), [self.home])
        self.assertEqual(
            set(library_files(self.user, hidden=True)), {self.colosseum, self.boat}
        )

    def test_a_photo_added_later_is_hidden_too(self):
        hide_files(self.user, [self.rome])

        later = make_photo(self.user, "forum.jpg", _at(2024, 5, 4), parent=self.rome)

        self.assertNotIn(later, library_files(self.user))

    def test_a_hidden_folder_follows_a_rename(self):
        hide_files(self.user, [self.rome])

        FileService.rename(self.rome, "Roma", acting_user=self.user)

        self.assertEqual(set(library_files(self.user)), {self.boat, self.home})

    def test_a_sibling_sharing_the_name_prefix_stays(self):
        rome_2 = FileService.create_folder(
            owner=self.user, name="Rome 2", parent=self.trips
        )
        kept = make_photo(self.user, "kept.jpg", _at(2024, 5, 5), parent=rome_2)

        hide_files(self.user, [self.rome])

        self.assertIn(kept, library_files(self.user))
        self.assertNotIn(self.colosseum, library_files(self.user))

    def test_a_wildcard_in_the_folder_name_matches_itself_only(self):
        """``_`` is a LIKE wildcard: a pattern match would hide "axb" with "a_b"."""
        a_b = FileService.create_folder(owner=self.user, name="a_b")
        axb = FileService.create_folder(owner=self.user, name="axb")
        kept = make_photo(self.user, "kept.jpg", _at(2024, 5, 5), parent=axb)
        gone = make_photo(self.user, "gone.jpg", _at(2024, 5, 6), parent=a_b)

        hide_files(self.user, [a_b])

        self.assertIn(kept, library_files(self.user))
        self.assertNotIn(gone, library_files(self.user))

    def test_a_personal_folder_leaves_a_group_folder_of_the_same_path_alone(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        group_root = FileService.create_folder(
            owner=self.user, name="Family", group=family
        )
        group_photo = make_photo(
            self.user, "picnic.jpg", _at(2024, 5, 5), parent=group_root
        )
        personal = FileService.create_folder(owner=self.user, name="Family")
        make_photo(self.user, "private.jpg", _at(2024, 5, 6), parent=personal)

        hide_files(self.user, [personal])

        self.assertIn(group_photo, library_files(self.user, ALL))
        self.assertEqual(list(library_groups(self.user)), [family])

    def test_a_group_folder_hides_the_group_photos_for_that_user_alone(self):
        bob = User.objects.create_user(username="bob", password="p")
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        bob.groups.add(family)
        group_root = FileService.create_folder(owner=bob, name="Family", group=family)
        group_photo = make_photo(bob, "picnic.jpg", _at(2024, 5, 5), parent=group_root)

        hide_files(self.user, [group_root])

        self.assertNotIn(group_photo, library_files(self.user, ALL))
        self.assertEqual(list(library_groups(self.user)), [])
        self.assertIn(group_photo, library_files(bob, family))

    def test_another_users_folder_of_the_same_path_is_left_alone(self):
        carol = User.objects.create_user(username="carol", password="p")
        theirs = FileService.create_folder(owner=carol, name="Trips")
        shared = make_photo(carol, "shared.jpg", _at(2024, 5, 5), parent=theirs)
        share_file(
            shared,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=carol,
        )

        hide_files(self.user, [self.trips])

        self.assertIn(shared, library_files(self.user, ALL))

    def test_hidden_folders_lists_the_reachable_ones_by_path(self):
        hide_files(self.user, [self.trips, self.rome])
        trashed = FileService.create_folder(owner=self.user, name="Old")
        hide_files(self.user, [trashed])
        FileService.soft_delete(trashed, acting_user=self.user)

        self.assertEqual(list(hidden_folders(self.user)), [self.trips, self.rome])


@skipUnless(connection.vendor == "sqlite", "counts SQLite VM steps")
class HiddenFilterCostTests(TestCase):
    """The hidden filter costs the same whatever the number of folders.

    The work SQLite does is counted in VM steps: a timing would be flaky.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        hide_files(self.user, [FileService.create_folder(owner=self.user, name="X")])
        photos = File.objects.bulk_create(
            File(
                owner=self.user,
                name=f"{i}.jpg",
                node_type=File.NodeType.FILE,
                path=f"{i}.jpg",
                type="jpeg",
                content=f"{i}.jpg",
            )
            for i in range(50)
        )
        MediaItem.objects.bulk_create(
            MediaItem(
                file=photo,
                media_type=MediaItem.MediaType.PHOTO,
                analyzed_at=timezone.now(),
            )
            for photo in photos
        )

    def _steps(self):
        steps = 0

        def tick():
            nonlocal steps
            steps += 1
            return 0

        connection.ensure_connection()
        connection.connection.set_progress_handler(tick, 100)
        try:
            self.assertEqual(library_files(self.user).count(), 50)
        finally:
            connection.connection.set_progress_handler(None, 0)
        return steps

    def test_more_folders_cost_the_library_nothing(self):
        few = self._steps()
        File.objects.bulk_create(
            File(
                owner=self.user,
                name=f"d{i}",
                node_type=File.NodeType.FOLDER,
                path=f"d{i}",
            )
            for i in range(500)
        )

        self.assertLess(self._steps(), few * 2)


class HiddenApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        self.folder = FileService.create_folder(owner=self.user, name="Private")
        self.photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14))

    def _hidden(self):
        return set(
            HiddenFile.objects.filter(owner=self.user).values_list("file_id", flat=True)
        )

    def test_hides_photos_and_folders(self):
        response = self.client.post(
            HIDE_URL,
            {"files": [str(self.photo.uuid), str(self.folder.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"hidden": 2})
        self.assertEqual(self._hidden(), {self.photo.uuid, self.folder.uuid})

    def test_a_file_out_of_reach_refuses_the_whole_batch(self):
        bob = User.objects.create_user(username="bob", password="p")
        theirs = make_photo(bob, "b.jpg", _at(2024, 7, 14))

        response = self.client.post(
            HIDE_URL,
            {"files": [str(self.photo.uuid), str(theirs.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._hidden(), set())

    def test_a_file_that_is_no_photo_is_refused(self):
        notes = upload(self.user, "notes.txt", b"hello")

        response = self.client.post(
            HIDE_URL, {"files": [str(notes.uuid)]}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)

    def test_a_malformed_body_is_refused(self):
        for body in ({}, {"files": []}, {"files": ["nope"]}):
            response = self.client.post(HIDE_URL, body, content_type="application/json")
            self.assertEqual(response.status_code, 400, body)

    def test_unhides(self):
        hide_files(self.user, [self.photo, self.folder])

        response = self.client.post(
            UNHIDE_URL,
            {"files": [str(self.photo.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.json(), {"unhidden": 1, "still_hidden": []})
        self.assertEqual(self._hidden(), {self.folder.uuid})

    def test_unhiding_names_what_a_hidden_folder_still_hides(self):
        inside = make_photo(self.user, "b.jpg", _at(2024, 7, 15), parent=self.folder)
        hide_files(self.user, [self.folder, inside, self.photo])

        response = self.client.post(
            UNHIDE_URL,
            {"files": [str(inside.uuid), str(self.photo.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(
            response.json(), {"unhidden": 2, "still_hidden": [str(inside.uuid)]}
        )
        self.assertNotIn(inside, library_files(self.user))

    def test_unhiding_never_touches_another_users_rows(self):
        bob = User.objects.create_user(username="bob", password="p")
        HiddenFile.objects.create(owner=bob, file=self.photo)

        self.client.post(
            UNHIDE_URL,
            {"files": [str(self.photo.uuid)]},
            content_type="application/json",
        )

        self.assertTrue(HiddenFile.objects.filter(owner=bob).exists())

    def test_lists_hidden_folders(self):
        hide_files(self.user, [self.folder, self.photo])

        response = self.client.get(FOLDERS_URL)

        self.assertEqual(
            response.json(),
            [
                {
                    "uuid": str(self.folder.uuid),
                    "name": "Private",
                    "path": "Private",
                    "group": None,
                }
            ],
        )

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(FOLDERS_URL)

        self.assertIn(response.status_code, (401, 403))


class HiddenViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        self.beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14))
        self.dog = make_photo(self.user, "dog.jpg", _at(2024, 7, 15))
        hide_files(self.user, [self.beach])

    def tearDown(self):
        cache.clear()

    def _tiles(self, response):
        return re.findall(r'data-uuid="([0-9a-f-]{36})"', response.content.decode())

    def test_the_timeline_leaves_hidden_photos_out(self):
        response = self.client.get("/photos")

        self.assertEqual(self._tiles(response), [str(self.dog.uuid)])

    def test_the_hidden_view_shows_them_alone(self):
        response = self.client.get("/photos?hidden=1")

        self.assertEqual(self._tiles(response), [str(self.beach.uuid)])
        self.assertContains(response, 'id="photos-hidden-view-data"')

    def test_the_hidden_view_reads_every_library(self):
        """What the user hid may sit in a group folder: no scope tab narrows it."""
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        root = FileService.create_folder(owner=self.user, name="Family", group=family)
        picnic = make_photo(self.user, "picnic.jpg", _at(2024, 7, 16), parent=root)
        hide_files(self.user, [picnic])

        response = self.client.get("/photos?hidden=1&scope=mine")

        self.assertEqual(
            self._tiles(response), [str(picnic.uuid), str(self.beach.uuid)]
        )

    @patch("workspace.photos.services.timeline.PAGE_SIZE", 2)
    def test_the_next_page_stays_on_the_hidden_view(self):
        for day in range(1, 4):
            hide_files(
                self.user, [make_photo(self.user, f"{day}.jpg", _at(2024, 6, day))]
            )

        first = self.client.get("/photos?hidden=1")
        next_url = _next_url(first)
        second = self.client.get(next_url)

        self.assertIn("hidden=1", next_url)
        self.assertEqual(len(self._tiles(first) + self._tiles(second)), 4)
        self.assertNotIn(str(self.dog.uuid), self._tiles(second))

    def test_the_sidebar_offers_hidden_only_while_the_preference_is_on(self):
        self.assertNotContains(self.client.get("/photos"), 'href="/photos?hidden=1"')

        set_setting(self.user, "photos", "show_hidden", True)

        self.assertContains(self.client.get("/photos"), 'href="/photos?hidden=1"')

    def test_the_hidden_view_keeps_its_own_entry_whatever_the_preference(self):
        response = self.client.get("/photos?hidden=1")

        self.assertContains(response, 'href="/photos?hidden=1"')

    def test_an_empty_hidden_view_says_how_to_hide(self):
        HiddenFile.objects.all().delete()

        response = self.client.get("/photos?hidden=1")

        self.assertContains(response, "Nothing hidden")

    def test_the_page_carries_the_hidden_folders(self):
        folder = FileService.create_folder(owner=self.user, name="Private")
        hide_files(self.user, [folder])

        response = self.client.get(f"/photos?scope={MINE}")

        self.assertContains(response, 'id="photos-hidden-folders-data"')
        self.assertContains(response, str(folder.uuid))
