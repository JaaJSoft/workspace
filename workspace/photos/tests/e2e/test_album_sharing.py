"""E2E cover for shared albums: what only a rendered page can prove.

The API and view tests pin who may do what; these check that the share
modal drives the endpoints, that a member reads photos they cannot open in
Files through the album (grid, viewer, menu), and that a public link works
without an account: its password, its expiry and its download setting.
Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.files.services.thumbnails.generation import get_thumbnail_path
from workspace.photos.models import AlbumLink, AlbumShare
from workspace.photos.services.album_sharing import create_link, share_album
from workspace.photos.services.albums import create_album
from workspace.photos.tests.images import jpeg_bytes, make_photo

TILES = "#timeline-grid [data-uuid]"


def _thumbnail(file_obj):
    default_storage.save(get_thumbnail_path(file_obj.uuid), ContentFile(jpeg_bytes()))
    File.objects.filter(pk=file_obj.pk).update(has_thumbnail=True)


class SharedAlbumTestCase(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it.
        self.owner = self.create_user(username="owner", is_staff=True)
        self.bob = self.create_user(username="bob", is_staff=True)
        self.photos = [
            make_photo(
                self.owner, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            for day in (14, 13, 12)
        ]
        for photo in self.photos:
            _thumbnail(photo)
        self.album = create_album(self.owner, "Summer", files=self.photos)

    def tearDown(self):
        cache.clear()
        super().tearDown()


class ShareModalTests(SharedAlbumTestCase):
    def test_sharing_with_a_contributor_from_the_modal(self):
        self.login_as(self.owner)
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        self.page.wait_for_selector(TILES)

        self.page.locator("[data-album-share]").click()
        modal = self.page.locator("[data-album-share-modal]")
        expect(modal).to_be_visible()
        expect(modal.locator("[data-album-owner]")).to_contain_text("owner")
        modal.get_by_placeholder("Search by username or name...").fill("bo")
        modal.get_by_text("bob", exact=True).first.click()
        member = modal.locator("[data-album-member='user:" + str(self.bob.pk) + "']")
        member.get_by_label("Role").select_option("contributor")
        modal.locator("[data-album-share-save]").click()

        # A closed daisyUI modal stays laid out, faded out: the attribute
        # is what says it closed.
        expect(modal).not_to_have_attribute("open", "")
        expect(self.page.locator("[data-album-sharing]")).to_contain_text("2 members")
        share = AlbumShare.objects.get(album=self.album)
        self.assertEqual((share.shared_with, share.role), (self.bob, "contributor"))

    def test_creating_a_public_link_from_the_modal(self):
        self.login_as(self.owner)
        self.context.grant_permissions(["clipboard-read", "clipboard-write"])
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        self.page.locator("[data-album-share]").click()
        modal = self.page.locator("[data-album-share-modal]")

        modal.get_by_role("button", name="Create link").click()
        modal.locator("[data-album-link-form] input[type=password]").fill("s3cret")
        modal.locator("[data-album-link-create]").click()

        expect(modal.locator("[data-album-link]")).to_have_count(1)
        expect(modal.locator("[data-album-link]")).to_contain_text("Password")
        link = AlbumLink.objects.get(album=self.album)
        self.assertTrue(link.has_password)
        self.assertFalse(link.allow_download)


class MemberViewTests(SharedAlbumTestCase):
    def setUp(self):
        super().setUp()
        share_album(
            self.album,
            role=AlbumShare.Role.VIEWER,
            acting_user=self.owner,
            user=self.bob,
        )
        self.login_as(self.bob)

    def test_a_member_browses_the_album_from_the_sidebar(self):
        self.page.goto(f"{self.live_server_url}/photos")

        shared = self.page.locator("[data-shared-albums]")
        expect(shared).to_contain_text("Summer")
        shared.get_by_text("Summer").click()

        self.page.wait_for_url(f"**/photos/albums/{self.album.uuid}")
        expect(self.page.locator(TILES)).to_have_count(3)
        expect(self.page.locator("[data-album-sharing]")).to_contain_text(
            "Shared by owner"
        )
        # The thumbnails come through the album, and they load.
        loaded = self.page.locator(f"{TILES} img").first
        expect(loaded).to_have_attribute(
            "src",
            f"/api/v1/photos/albums/{self.album.uuid}/files/{self.photos[0].uuid}/thumbnail",
        )
        self.page.wait_for_function(
            "(sel) => { const img = document.querySelector(sel); return img && img.complete && img.naturalWidth > 0; }",
            arg=f"{TILES} img",
        )

    def test_the_viewer_reads_the_photo_through_the_album(self):
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        self.page.wait_for_selector(TILES)

        self.page.locator(TILES).first.locator("button").first.click()

        image = self.page.locator("#viewer-panel img").first
        expect(image).to_be_visible()
        self.assertIn(
            f"/api/v1/photos/albums/{self.album.uuid}/files/",
            image.get_attribute("src"),
        )
        self.page.wait_for_function(
            "() => { const img = document.querySelector('#viewer-panel img'); return img && img.naturalWidth > 0; }"
        )

    def test_the_menu_of_a_photo_only_the_album_serves(self):
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        tile = self.page.locator(TILES).first
        tile.hover()
        tile.get_by_role("button", name="More actions").click()

        menu = self.page.locator("#photos-context-menu")
        expect(menu.get_by_text("View")).to_be_visible()
        expect(menu.get_by_text("Download")).to_be_visible()
        expect(menu.get_by_text("Open in Files")).to_have_count(0)
        expect(menu.get_by_text("Add to album")).to_have_count(0)
        expect(menu.get_by_text("Remove from album")).to_have_count(0)

    def test_leaving_the_album(self):
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        self.page.get_by_role("button", name="Album actions").click()
        self.page.locator("#photos-album-menu").get_by_text("Leave album").click()
        self.page.locator("#app-dialog-confirm-ok").click()

        self.page.wait_for_url(f"{self.live_server_url}/photos")
        self.assertFalse(AlbumShare.objects.filter(album=self.album).exists())


class PublicLinkTests(SharedAlbumTestCase):
    def _open(self, link, query=""):
        self.page.goto(f"{self.live_server_url}/photos/shared/{link.token}{query}")

    def test_a_visitor_browses_the_album_and_its_viewer(self):
        link = create_link(self.album, acting_user=self.owner)
        self._open(link)

        tiles = self.page.locator("[data-shared-photo]")
        expect(tiles).to_have_count(3)
        expect(self.page.locator("[data-shared-archive]")).to_have_count(0)
        tiles.first.click()

        expect(self.page.locator("[data-shared-viewer] img").first).to_be_visible()
        expect(self.page.locator("[data-shared-album]")).to_contain_text("1 / 3")
        expect(self.page.locator("[data-shared-download]")).to_have_count(0)
        self.page.keyboard.press("ArrowRight")
        expect(self.page.locator("[data-shared-album]")).to_contain_text("2 / 3")

    def test_the_password_unlocks_the_album(self):
        link = create_link(self.album, acting_user=self.owner, password="s3cret")
        self._open(link)

        password = self.page.get_by_placeholder("Password")
        password.fill("wrong")
        password.press("Enter")
        expect(self.page.get_by_text("Invalid password")).to_be_visible()
        password.fill("s3cret")
        password.press("Enter")

        expect(self.page.locator("[data-shared-photo]")).to_have_count(3)

    def test_downloads_follow_the_link_setting(self):
        link = create_link(self.album, acting_user=self.owner, allow_download=True)
        self._open(link)

        expect(self.page.locator("[data-shared-archive]")).to_be_visible()
        self.page.locator("[data-shared-photo]").first.click()
        expect(self.page.locator("[data-shared-download]")).to_be_visible()

    def test_an_expired_link(self):
        link = create_link(self.album, acting_user=self.owner)
        AlbumLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self._open(link)

        expect(self.page.get_by_text("Link expired")).to_be_visible()
        expect(self.page.locator("[data-shared-photo]")).to_have_count(0)

    def test_a_password_set_after_the_fact_is_asked_for(self):
        link = create_link(self.album, acting_user=self.owner)
        AlbumLink.objects.filter(pk=link.pk).update(password=make_password("later"))
        self._open(link)

        expect(self.page.get_by_text("Protected link")).to_be_visible()
