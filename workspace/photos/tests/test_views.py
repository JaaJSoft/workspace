import json
import re
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.files.models import FileFavorite, FileScan, FileShare, FileTag, Tag
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.photos.models import Album, MediaItem
from workspace.photos.services.albums import create_album
from workspace.users.services.settings import set_setting

from .images import make_photo, make_video, upload

User = get_user_model()


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def _next_url(response):
    """The url the page's scroll sentinel will fetch, or None on the last page."""
    match = re.search(r"timelineSentinel\('([^']+)'\)", response.content.decode())
    # The url is written through |escapejs, as a JS string literal.
    return match and json.loads(f'"{match[1]}"')


class PhotosViewTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _tiles(self, response):
        """The uuids of the photo tiles, in page order."""
        return re.findall(r'data-uuid="([0-9a-f-]{36})"', response.content.decode())


class IndexTests(PhotosViewTestCase):
    def test_login_required(self):
        self.client.logout()

        response = self.client.get("/photos")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_timeline_renders_photos_under_their_dates(self):
        beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        scan = make_photo(self.user, "scan.png", None)

        response = self.client.get("/photos")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "photos/ui/index.html")
        self.assertEqual(self._tiles(response), [str(beach.uuid), str(scan.uuid)])
        self.assertContains(response, "July 2024")
        self.assertContains(response, 'data-day="2024-07-14"')
        self.assertContains(response, "data-undated")
        self.assertContains(response, "2 photos")

    def test_empty_library(self):
        response = self.client.get("/photos")

        self.assertContains(response, "No photos yet")
        self.assertEqual(self._tiles(response), [])

    def test_only_the_users_own_personal_photos(self):
        mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14, 12))
        bob = User.objects.create_user(username="bob", password="p")
        make_photo(bob, "bobs.jpg", _at(2024, 7, 14, 12))
        group = Group.objects.create(name="Family")
        self.user.groups.add(group)
        group_root = FileService.create_folder(owner=bob, name="Family", group=group)
        make_photo(bob, "family.jpg", _at(2024, 7, 14, 13), parent=group_root)

        self.assertEqual(self._tiles(self.client.get("/photos")), [str(mine.uuid)])

    def test_an_image_not_analyzed_yet_is_counted_not_shown(self):
        upload(self.user, "fresh.jpg")

        response = self.client.get("/photos")

        self.assertEqual(self._tiles(response), [])
        self.assertContains(response, "1 being analyzed")

    def test_tiles_offer_the_favorite_star(self):
        make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))

        response = self.client.get("/photos")

        self.assertContains(response, 'data-can-favorite="1"')
        self.assertContains(response, 'aria-label="Add to favorites"')

    def test_tiles_carry_what_the_viewer_modal_navigates_by(self):
        beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))

        response = self.client.get("/photos")

        self.assertContains(response, 'x-data="fileViewerModal()"')
        self.assertContains(response, f'data-uuid="{beach.uuid}"')
        self.assertContains(response, 'data-viewable="1"')
        self.assertContains(response, 'data-node-type="file"')
        self.assertContains(response, 'data-display-name="beach.jpg"')

    def test_query_count_does_not_grow_with_the_page(self):
        tag = Tag.objects.create(owner=self.user, name="Summer")
        make_photo(self.user, "first.jpg", _at(2024, 7, 14, 12))
        self.client.get("/photos")  # warms the per-user settings cache

        with CaptureQueriesContext(connection) as small:
            self.client.get("/photos")
        for day in range(1, 13):
            f = make_photo(self.user, f"p{day}.jpg", _at(2024, 6, day, 12))
            FileTag.objects.create(file=f, tag=tag)
            FileFavorite.objects.create(owner=self.user, file=f)
        with CaptureQueriesContext(connection) as large:
            self.client.get("/photos")

        self.assertEqual(len(large), len(small))

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_quarantined_photo_is_hidden(self):
        f = make_photo(self.user, "bad.jpg", _at(2024, 7, 14, 12))
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertEqual(self._tiles(self.client.get("/photos")), [])


class TrashTests(PhotosViewTestCase):
    def test_trashing_hides_and_restoring_brings_back_without_a_write(self):
        f = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        row = MediaItem.objects.get(file=f)

        FileService.soft_delete(f, acting_user=self.user)
        self.assertEqual(self._tiles(self.client.get("/photos")), [])

        f.refresh_from_db()
        FileService.restore(f, acting_user=self.user)
        self.assertEqual(self._tiles(self.client.get("/photos")), [str(f.uuid)])

        after = MediaItem.objects.get(file=f)
        self.assertEqual(
            (after.pk, after.taken_at, after.analyzed_at),
            (row.pk, row.taken_at, row.analyzed_at),
        )

    def test_trashing_a_folder_hides_the_photos_in_it(self):
        folder = FileService.create_folder(owner=self.user, name="Holidays")
        make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12), parent=folder)

        FileService.soft_delete(folder, acting_user=self.user)

        self.assertEqual(self._tiles(self.client.get("/photos")), [])

    def test_hard_deletion_cascades(self):
        f = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))

        FileService.hard_delete(f, acting_user=self.user)

        self.assertFalse(MediaItem.objects.exists())


class FilterTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        self.dinner = make_photo(self.user, "dinner.jpg", _at(2024, 7, 14, 20))

    def test_favorites(self):
        FileFavorite.objects.create(owner=self.user, file=self.beach)

        response = self.client.get("/photos?favorites=1")

        self.assertEqual(self._tiles(response), [str(self.beach.uuid)])
        self.assertContains(response, "<h1", count=1)
        self.assertContains(response, "Favorites")

    def test_someone_elses_favorite_does_not_count(self):
        bob = User.objects.create_user(username="bob", password="p")
        FileFavorite.objects.create(owner=bob, file=self.beach)

        self.assertEqual(self._tiles(self.client.get("/photos?favorites=1")), [])

    def test_favorites_false_is_no_filter(self):
        self.assertEqual(
            len(self._tiles(self.client.get("/photos?favorites=false"))), 2
        )

    def test_tag(self):
        tag = Tag.objects.create(owner=self.user, name="Summer")
        FileTag.objects.create(file=self.dinner, tag=tag)

        response = self.client.get(f"/photos?tag={tag.uuid}")

        self.assertEqual(self._tiles(response), [str(self.dinner.uuid)])

    def test_sidebar_lists_the_tags_that_carry_photos(self):
        summer = Tag.objects.create(owner=self.user, name="Summer")
        FileTag.objects.create(file=self.dinner, tag=summer)
        Tag.objects.create(owner=self.user, name="Invoices")

        response = self.client.get("/photos")

        self.assertContains(response, f"?tag={summer.uuid}")
        self.assertNotContains(response, "Invoices")

    def test_unknown_malformed_or_foreign_tag_is_404(self):
        bob = User.objects.create_user(username="bob", password="p")
        foreign = Tag.objects.create(owner=bob, name="Bob's")
        for value in ("00000000-0000-0000-0000-000000000000", "nope", foreign.uuid):
            with self.subTest(value=value):
                response = self.client.get(f"/photos?tag={value}")
                self.assertEqual(response.status_code, 404)

    def test_sidebar_years(self):
        make_photo(self.user, "old.jpg", _at(2019, 3, 1, 12))
        make_photo(self.user, "scan.png", None)

        response = self.client.get("/photos")

        self.assertContains(response, 'href="/photos?date=2024"')
        self.assertContains(response, 'href="/photos?date=2019"')
        self.assertContains(response, 'href="/photos?date=undated"')


class VideoTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        self.surf = make_video(
            self.user, "surf.webm", _at(2024, 7, 14, 15), duration=754.4
        )

    def _badges(self, response):
        return re.findall(
            r"data-duration-badge.*?<span>([^<]+)</span>",
            response.content.decode(),
            re.S,
        )

    def test_videos_sit_in_the_timeline_with_the_photos(self):
        response = self.client.get("/photos")

        self.assertEqual(
            self._tiles(response), [str(self.surf.uuid), str(self.beach.uuid)]
        )
        self.assertContains(response, "1 photo, 1 video")

    def test_a_video_tile_shows_its_length(self):
        self.assertEqual(self._badges(self.client.get("/photos")), ["12:34"])

    def test_the_viewer_pages_through_videos_too(self):
        response = self.client.get("/photos")

        self.assertContains(
            response,
            f'data-uuid="{self.surf.uuid}"\n  data-node-type="file"\n  data-viewable="1"',
        )

    def test_videos_view(self):
        response = self.client.get("/photos?type=video")

        self.assertEqual(self._tiles(response), [str(self.surf.uuid)])
        self.assertContains(response, "1 video")
        self.assertNotContains(response, "1 photo")

    def test_photos_view(self):
        response = self.client.get("/photos?type=photo")

        self.assertEqual(self._tiles(response), [str(self.beach.uuid)])
        self.assertContains(response, "1 photo")
        self.assertNotContains(response, "1 video")

    def test_videos_view_keeps_its_filter_on_the_next_page(self):
        for day in range(3):
            make_video(self.user, f"clip{day}.webm", _at(2024, 6, 1 + day, 9))

        with patch("workspace.photos.services.timeline.PAGE_SIZE", 2):
            response = self.client.get("/photos?type=video")
            next_page = self.client.get(_next_url(response))

        self.assertIn("type=video", _next_url(response))
        self.assertTrue(self._tiles(next_page))
        self.assertNotIn(str(self.beach.uuid), self._tiles(next_page))

    def test_unknown_media_type_is_400(self):
        for value in ("audio", "videos", "1"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.client.get(f"/photos?type={value}").status_code, 400
                )
                self.assertEqual(
                    self.client.get(f"/photos/timeline?type={value}").status_code,
                    400,
                )

    def test_the_sidebar_leaves_the_media_type_to_the_header(self):
        html = self.client.get("/photos").content.decode()
        sidebar = re.search(r'id="photos-nav".*?</nav>', html, re.S)[0]

        self.assertNotIn("type=video", sidebar)
        self.assertIn('href="/photos?type=video"', html)


class MediaTypeTabsTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.beach = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))
        self.surf = make_video(self.user, "surf.webm", _at(2024, 7, 14, 15))

    def test_tabs_offer_all_photos_and_videos(self):
        response = self.client.get("/photos?type=video")

        tabs = response.context["type_tabs"]
        self.assertEqual([t["label"] for t in tabs], ["All", "Photos", "Videos"])
        self.assertEqual([t["active"] for t in tabs], [False, False, True])
        self.assertEqual(
            [t["url"] for t in tabs],
            ["/photos", "/photos?type=photo", "/photos?type=video"],
        )
        self.assertContains(response, 'aria-label="Media type"')

    def test_the_type_stacks_on_the_view_and_the_scope(self):
        FileFavorite.objects.create(owner=self.user, file=self.beach)
        FileFavorite.objects.create(owner=self.user, file=self.surf)

        response = self.client.get("/photos?scope=all&favorites=1&type=photo")

        self.assertEqual(self._tiles(response), [str(self.beach.uuid)])
        self.assertEqual(response.context["title"], "Favorites")
        self.assertEqual(
            [t["url"] for t in response.context["type_tabs"]],
            [
                "/photos?scope=all&favorites=1",
                "/photos?scope=all&favorites=1&type=photo",
                "/photos?scope=all&favorites=1&type=video",
            ],
        )

    def test_the_type_drops_the_date(self):
        response = self.client.get("/photos?date=2024")

        self.assertEqual(response.context["type_tabs"][2]["url"], "/photos?type=video")

    def test_the_sidebar_links_keep_the_type(self):
        tag = Tag.objects.create(owner=self.user, name="Summer")
        FileTag.objects.create(file=self.surf, tag=tag)

        response = self.client.get("/photos?type=video")

        self.assertEqual(response.context["timeline_url"], "/photos?type=video")
        self.assertEqual(
            response.context["favorites_url"], "/photos?favorites=1&type=video"
        )
        self.assertEqual(
            response.context["tags"][0]["url"], f"/photos?tag={tag.uuid}&type=video"
        )
        self.assertContains(response, 'href="/photos?type=video&amp;date=2024"')

    def test_no_tabs_while_the_view_holds_a_single_kind(self):
        self.surf.soft_delete()

        response = self.client.get("/photos")

        self.assertEqual(response.context["type_tabs"], [])
        self.assertNotContains(response, 'aria-label="Media type"')

    def test_the_tabs_stay_once_a_type_is_picked(self):
        self.surf.soft_delete()

        response = self.client.get("/photos?type=video")

        self.assertEqual(self._tiles(response), [])
        self.assertEqual(len(response.context["type_tabs"]), 3)
        self.assertContains(response, "No video matches this view.")


class CountLabelTests(PhotosViewTestCase):
    def test_empty_library(self):
        self.assertContains(self.client.get("/photos"), "0 photos")

    def test_only_videos(self):
        make_video(self.user, "a.webm", _at(2024, 7, 14, 12))
        make_video(self.user, "b.webm", _at(2024, 7, 14, 13))

        response = self.client.get("/photos")

        self.assertContains(response, "2 videos")
        self.assertNotContains(response, "0 photos")


class DateTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.recent = make_photo(self.user, "recent.jpg", _at(2024, 7, 14, 12))
        self.old = make_photo(self.user, "old.jpg", _at(2019, 3, 1, 12))

    def test_date_opens_the_timeline_there(self):
        response = self.client.get("/photos?date=2019")

        self.assertEqual(self._tiles(response), [str(self.old.uuid)])
        self.assertContains(response, "Back to latest")

    def test_invalid_date_is_400(self):
        for value in ("2019-13", "soon", "2019-02-30"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.client.get(f"/photos?date={value}").status_code, 400
                )


@patch("workspace.photos.services.timeline.PAGE_SIZE", 2)
class PaginationTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.photos = [
            make_photo(self.user, f"p{day}.jpg", _at(2024, 7, day, 12))
            for day in (14, 13, 12)
        ]

    def test_scrolling_walks_the_whole_timeline(self):
        first = self.client.get("/photos")
        next_url = _next_url(first)
        self.assertTrue(next_url.startswith("/photos/timeline?cursor="))

        second = self.client.get(next_url)

        self.assertEqual(second.status_code, 200)
        self.assertTemplateUsed(second, "photos/ui/partials/timeline_page.html")
        self.assertTemplateNotUsed(second, "photos/ui/index.html")
        self.assertEqual(
            self._tiles(first) + self._tiles(second),
            [str(f.uuid) for f in self.photos],
        )
        # The month goes on: the next page opens with a day, not a month.
        self.assertContains(second, 'data-day="2024-07-12"')
        self.assertNotContains(second, "data-month=")
        self.assertIsNone(_next_url(second))
        self.assertContains(second, 'id="timeline-grid"')
        self.assertContains(second, 'id="timeline-more"')

    def test_next_page_keeps_the_filters(self):
        for f in self.photos:
            FileFavorite.objects.create(owner=self.user, file=f)

        next_url = _next_url(self.client.get("/photos?favorites=1"))

        self.assertIn("favorites=1", next_url)

    def test_invalid_cursor_is_400(self):
        for value in ("", "nope", "d1.zzz"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.client.get(f"/photos/timeline?cursor={value}").status_code,
                    400,
                )

    def test_timeline_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.get("/photos/timeline?cursor=x").status_code, 302)


class ScopeTabsTests(PhotosViewTestCase):
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

    def test_mine_by_default(self):
        self.assertEqual(self._tiles(self.client.get("/photos")), [str(self.mine.uuid)])

    def test_all_reads_mine_and_my_groups(self):
        response = self.client.get("/photos?scope=all")

        self.assertEqual(
            self._tiles(response), [str(self.mine.uuid), str(self.family_photo.uuid)]
        )

    def test_a_group_reads_its_folder(self):
        response = self.client.get(f"/photos?scope=group:{self.family.pk}")

        self.assertEqual(self._tiles(response), [str(self.family_photo.uuid)])

    def test_a_group_the_user_is_not_in_or_a_malformed_scope_is_404(self):
        strangers = Group.objects.create(name="Strangers")
        for value in (f"group:{strangers.pk}", "group:abc", "group:", "everything"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.client.get(f"/photos?scope={value}").status_code, 404
                )

    def test_tabs_offer_mine_all_and_the_groups_holding_photos(self):
        Group.objects.create(name="Empty").user_set.add(self.user)

        response = self.client.get("/photos?favorites=1")

        tabs = response.context["scope_tabs"]
        self.assertEqual([t["label"] for t in tabs], ["Mine", "All", "Family"])
        self.assertEqual([t["active"] for t in tabs], [True, False, False])
        # The view filter carries over to the other libraries.
        self.assertEqual(
            [t["url"] for t in tabs],
            [
                "/photos?favorites=1",
                "/photos?scope=all&favorites=1",
                f"/photos?scope=group%3A{self.family.pk}&favorites=1",
            ],
        )

    def test_no_tabs_when_there_is_nothing_to_switch_to(self):
        self.family_photo.parent.soft_delete()

        response = self.client.get("/photos")

        self.assertEqual(response.context["scope_tabs"], [])
        self.assertNotContains(response, 'aria-label="Library"')

    def test_sidebar_links_stay_in_the_scope(self):
        tag = Tag.objects.create(owner=self.user, name="Summer")
        FileTag.objects.create(file=self.family_photo, tag=tag)

        response = self.client.get("/photos?scope=all")

        self.assertEqual(
            response.context["favorites_url"], "/photos?scope=all&favorites=1"
        )
        self.assertEqual(response.context["timeline_url"], "/photos?scope=all")
        self.assertEqual(
            response.context["tags"][0]["url"], f"/photos?scope=all&tag={tag.uuid}"
        )
        self.assertContains(response, 'href="/photos?scope=all&amp;date=2024"')

    @patch("workspace.photos.services.timeline.PAGE_SIZE", 1)
    def test_the_next_page_stays_in_the_scope(self):
        first = self.client.get("/photos?scope=all")
        next_url = _next_url(first)
        self.assertIn("scope=all", next_url)

        second = self.client.get(next_url)

        self.assertEqual(self._tiles(second), [str(self.family_photo.uuid)])


class SharedScopeTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.carol = User.objects.create_user(username="carol", password="p")
        self.mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14, 12))
        self.shared = make_photo(self.carol, "shared.jpg", _at(2024, 7, 13, 12))
        share_file(
            self.shared,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.carol,
        )

    def test_shared_with_me_gets_a_tab_even_without_groups(self):
        tabs = self.client.get("/photos").context["scope_tabs"]

        self.assertEqual(
            [(t["label"], t["url"]) for t in tabs],
            [
                ("Mine", "/photos"),
                ("All", "/photos?scope=all"),
                ("Shared with me", "/photos?scope=shared"),
            ],
        )

    def test_shared_reads_the_photos_shared_with_me(self):
        response = self.client.get("/photos?scope=shared")

        self.assertEqual(self._tiles(response), [str(self.shared.uuid)])

    def test_all_includes_what_is_shared_with_me(self):
        response = self.client.get("/photos?scope=all")

        self.assertEqual(
            self._tiles(response), [str(self.mine.uuid), str(self.shared.uuid)]
        )


class FilesLinkTests(PhotosViewTestCase):
    """Where a tile's "Open in Files" lands, whoever owns the file."""

    def _files_url(self, response, file_obj):
        match = re.search(
            rf'data-uuid="{file_obj.uuid}"[^>]*?data-files-url="([^"]+)"',
            response.content.decode(),
            re.S,
        )
        return match[1].replace("&amp;", "&")

    def test_own_photo_opens_in_its_folder(self):
        folder = FileService.create_folder(owner=self.user, name="Holidays")
        f = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12), parent=folder)

        response = self.client.get("/photos")

        self.assertEqual(
            self._files_url(response, f), f"/files/{folder.uuid}?open={f.uuid}"
        )

    def test_photo_at_the_root_opens_in_my_files(self):
        f = make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))

        self.assertEqual(
            self._files_url(self.client.get("/photos"), f), f"/files?open={f.uuid}"
        )

    def test_group_photo_opens_in_the_group_folder(self):
        bob = User.objects.create_user(username="bob", password="p")
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        root = FileService.create_folder(owner=bob, name="Family", group=family)
        f = make_photo(bob, "family.jpg", _at(2024, 7, 14, 12), parent=root)

        response = self.client.get("/photos?scope=all")

        self.assertEqual(
            self._files_url(response, f), f"/files/{root.uuid}?open={f.uuid}"
        )

    def test_photo_shared_on_its_own_opens_in_shared_with_me(self):
        carol = User.objects.create_user(username="carol", password="p")
        private = FileService.create_folder(owner=carol, name="Private")
        f = make_photo(carol, "shared.jpg", _at(2024, 7, 14, 12), parent=private)
        share_file(
            f,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=carol,
        )

        response = self.client.get("/photos?scope=shared")

        self.assertEqual(self._files_url(response, f), f"/files?shared=1&open={f.uuid}")
        self.assertNotContains(response, str(private.uuid))


class FilesComponentsTests(PhotosViewTestCase):
    def test_the_page_carries_the_files_panel_menu_and_dialogs(self):
        make_photo(self.user, "beach.jpg", _at(2024, 7, 14, 12))

        response = self.client.get("/photos")

        for marker in (
            'id="properties-sidebar"',
            'id="properties-content"',
            'id="photos-context-menu"',
            'id="rename-dialog"',
            'id="tag-dialog"',
            'x-data="shareModal()"',
            "files/ui/js/properties_panel.js",
            "files/ui/js/tags.js",
            "files/ui/js/share_modal.js",
            "ui/js/comments.js",
        ):
            with self.subTest(marker=marker):
                self.assertContains(response, marker)


class TileSizeTests(PhotosViewTestCase):
    def _tile(self, response):
        match = re.search(
            r'<script id="photo-tile-data" type="application/json">(.*?)</script>',
            response.content.decode(),
        )
        return json.loads(match[1])

    def test_the_default_step_until_the_user_picks_one(self):
        response = self.client.get("/photos")

        self.assertEqual(
            self._tile(response), {"size": 3, "widths": [96, 120, 144, 192, 256]}
        )
        self.assertContains(response, 'style="--photo-tile: 144px"')

    def test_the_page_opens_at_the_stored_step(self):
        set_setting(self.user, "photos", "tile_size", 5)

        response = self.client.get("/photos")

        self.assertEqual(self._tile(response)["size"], 5)
        self.assertContains(response, 'style="--photo-tile: 256px"')

    def test_a_stored_value_off_the_slider_falls_back_to_the_default(self):
        for value in (0, 6, "4", True, None, [2]):
            with self.subTest(value=value):
                set_setting(self.user, "photos", "tile_size", value)

                response = self.client.get("/photos")

                self.assertEqual(self._tile(response)["size"], 3)
                self.assertContains(response, 'style="--photo-tile: 144px"')


class AlbumPageTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.first = make_photo(self.user, "first.jpg", _at(2024, 7, 14, 12))
        self.second = make_photo(self.user, "second.jpg", _at(2024, 8, 2, 12))
        self.outside = make_photo(self.user, "outside.jpg", _at(2024, 8, 3, 12))
        self.album = create_album(self.user, "Summer", files=[self.first, self.second])
        self.url = f"/photos/albums/{self.album.uuid}"

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_the_album_by_capture_date(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._tiles(response), [str(self.second.uuid), str(self.first.uuid)]
        )
        self.assertContains(response, "August 2024")
        self.assertContains(response, "2 photos")
        self.assertContains(response, "14 Jul - 2 Aug 2024")
        self.assertContains(response, "By capture date")
        self.assertContains(response, 'id="album-data"')
        self.assertNotContains(response, 'draggable="true"')

    def test_someone_elses_album_is_not_found(self):
        bob = User.objects.create_user(username="bob", password="p")
        bobs = create_album(bob, "Bob's", files=[])

        self.assertEqual(
            self.client.get(f"/photos/albums/{bobs.uuid}").status_code, 404
        )
        self.assertEqual(
            self.client.get(
                f"/photos/albums/{bobs.uuid}/timeline?cursor=x"
            ).status_code,
            404,
        )

    def test_a_trashed_photo_drops_out_of_the_album(self):
        FileService.soft_delete(self.first, acting_user=self.user)

        response = self.client.get(self.url)

        self.assertEqual(self._tiles(response), [str(self.second.uuid)])
        self.assertContains(response, "1 photo")

    def test_manual_order(self):
        self.album.sort_mode = Album.SortMode.MANUAL
        self.album.save(update_fields=["sort_mode"])

        response = self.client.get(self.url)

        self.assertEqual(
            self._tiles(response), [str(self.first.uuid), str(self.second.uuid)]
        )
        self.assertContains(response, "Manual order")
        self.assertContains(response, 'draggable="true"')
        self.assertNotContains(response, "data-month")

    def test_empty_album(self):
        empty = create_album(self.user, "Empty")

        response = self.client.get(f"/photos/albums/{empty.uuid}")

        self.assertContains(response, "This album is empty")
        self.assertEqual(self._tiles(response), [])

    def test_the_sidebar_lists_the_albums_and_marks_the_open_one(self):
        other = create_album(self.user, "Autumn", files=[self.outside])

        response = self.client.get(self.url)

        albums = re.findall(r'data-album="([0-9a-f-]{36})"', response.content.decode())
        self.assertEqual(albums, [str(other.uuid), str(self.album.uuid)])
        self.assertRegex(
            response.content.decode(),
            rf'aria-current="page"\s+data-album="{self.album.uuid}"',
        )

    def test_the_timeline_lists_the_albums_too(self):
        response = self.client.get("/photos")

        self.assertContains(response, f'data-album="{self.album.uuid}"')
        self.assertContains(response, "New album")


@patch("workspace.photos.services.timeline.PAGE_SIZE", 2)
class AlbumPaginationTests(PhotosViewTestCase):
    def setUp(self):
        super().setUp()
        self.photos = [
            make_photo(self.user, f"p{day}.jpg", _at(2024, 7, day, 12))
            for day in range(1, 6)
        ]
        self.album = create_album(self.user, "Trip", files=self.photos)
        self.url = f"/photos/albums/{self.album.uuid}"

    def _walk(self):
        response = self.client.get(self.url)
        tiles = self._tiles(response)
        next_url = _next_url(response)
        while next_url:
            response = self.client.get(next_url)
            self.assertEqual(response.status_code, 200)
            tiles += self._tiles(response)
            next_url = _next_url(response)
        return tiles

    def test_capture_date_pages(self):
        self.assertEqual(self._walk(), [str(p.uuid) for p in reversed(self.photos)])

    def test_manual_pages(self):
        self.album.sort_mode = Album.SortMode.MANUAL
        self.album.save(update_fields=["sort_mode"])

        tiles = self._walk()

        self.assertEqual(tiles, [str(p.uuid) for p in self.photos])

    def test_a_cursor_of_the_other_sort_mode_is_refused(self):
        response = self.client.get(
            f"{self.url}/timeline?cursor=m65536.{self.photos[0].uuid}"
        )

        self.assertEqual(response.status_code, 400)
