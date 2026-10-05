"""The album pages as a member sees them: the sidebar's shared albums, the
header's members, and tiles read through the album when Files would not
serve them."""

import json
import re
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import File
from workspace.photos.models import Album
from workspace.photos.services.album_sharing import share_album
from workspace.photos.services.albums import add_items, create_album

from .images import make_photo

User = get_user_model()


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class AlbumPageTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.photo = make_photo(self.owner, "beach.jpg", _at(14))
        File.objects.filter(pk=self.photo.pk).update(has_thumbnail=True)
        self.album = create_album(self.owner, "Trip", files=[self.photo])

    def tearDown(self):
        cache.clear()

    def page(self, user, path=""):
        self.client.force_login(user)
        response = self.client.get(f"/photos/albums/{self.album.uuid}{path}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def tile(self, html, file_obj):
        start = html.index(f'data-uuid="{file_obj.uuid}"')
        return html[start : html.index(">", html.index("data-selected", start))]


class SharedAlbumPageTests(AlbumPageTestCase):
    def test_a_viewer_reads_the_owners_photos_through_the_album(self):
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)

        html = self.page(self.bob)
        tile = self.tile(html, self.photo)

        base = f"/api/v1/photos/albums/{self.album.uuid}/files/{self.photo.uuid}"
        self.assertIn(f"{base}/thumbnail", html)
        self.assertIn('data-album-scoped="1"', tile)
        self.assertIn(
            f'data-viewer-url="/photos/albums/{self.album.uuid}/view/{self.photo.uuid}"',
            tile,
        )
        self.assertIn(f'data-content-url="{base}/content"', tile)
        self.assertIn(f'data-download-url="{base}/download"', tile)
        self.assertIn('data-files-url=""', tile)
        self.assertIn('data-removable="0"', tile)
        self.assertNotIn(f"/api/v1/files/{self.photo.uuid}/thumbnail", html)

    def test_no_download_url_when_the_album_forbids_it(self):
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)
        Album.objects.filter(pk=self.album.pk).update(allow_download=False)

        tile = self.tile(self.page(self.bob), self.photo)

        self.assertIn('data-download-url=""', tile)

    def test_a_contributor_may_remove_what_they_added(self):
        share_album(
            self.album, role="contributor", acting_user=self.owner, user=self.bob
        )
        bobs = make_photo(self.bob, "bob.jpg", _at(15))
        add_items(self.album, [bobs], added_by=self.bob)

        html = self.page(self.bob)

        own = self.tile(html, bobs)
        self.assertIn('data-removable="1"', own)
        self.assertNotIn("data-album-scoped", own)
        self.assertIn('data-files-url="/files?open=', own)
        self.assertIn('data-removable="0"', self.tile(html, self.photo))

    def test_the_owner_reads_their_photos_through_files(self):
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)

        tile = self.tile(self.page(self.owner), self.photo)

        self.assertNotIn("data-album-scoped", tile)
        self.assertIn('data-removable="1"', tile)

    def test_the_header_names_the_members_and_who_shared_it(self):
        share_album(
            self.album, role="contributor", acting_user=self.owner, user=self.bob
        )

        html = self.page(self.bob)

        self.assertIn("2 members", html)
        self.assertIn("Shared by owner", html)
        self.assertIn("data-album-role", html)
        owner_view = self.page(self.owner)
        self.assertIn("2 members", owner_view)
        self.assertNotIn("Shared by", owner_view)

    def test_an_album_nobody_else_opens_names_no_members(self):
        self.assertNotIn("data-album-sharing", self.page(self.owner))

    def test_the_sidebar_lists_shared_albums_apart(self):
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)
        create_album(self.bob, "Bob's own")

        html = self.page(self.bob)

        shared = html[html.index("data-shared-albums") :]
        own = html[html.index("data-albums") : html.index("data-shared-albums")]
        self.assertIn("Bob&#x27;s own", own)
        self.assertNotIn("Trip", own)
        self.assertIn("Trip", shared)
        self.assertIn('title="Shared by owner"', shared)

    def test_the_owner_sees_no_shared_group(self):
        self.assertNotIn("data-shared-albums", self.page(self.owner))

    def test_the_next_page_keeps_the_album_tiles(self):
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)
        Album.objects.filter(pk=self.album.pk).update(sort_mode=Album.SortMode.MANUAL)
        more = [make_photo(self.owner, f"{i}.jpg", _at(1)) for i in range(60)]
        add_items(self.album, more, added_by=self.owner)
        self.client.force_login(self.bob)
        first = self.client.get(f"/photos/albums/{self.album.uuid}").content.decode()
        # Written through |escapejs, as a JS string literal.
        next_url = json.loads(
            '"' + re.search(r"timelineSentinel\('([^']+)'\)", first)[1] + '"'
        )

        response = self.client.get(next_url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn('data-album-scoped="1"', html)


class AlbumViewerPanelTests(AlbumPageTestCase):
    def setUp(self):
        super().setUp()
        share_album(self.album, role="viewer", acting_user=self.owner, user=self.bob)

    def viewer(self, user, file_obj, album=None):
        self.client.force_login(user)
        return self.client.get(
            f"/photos/albums/{(album or self.album).uuid}/view/{file_obj.uuid}"
        )

    def test_a_member_gets_the_viewer_reading_through_the_album(self):
        response = self.viewer(self.bob, self.photo)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"/api/v1/photos/albums/{self.album.uuid}/files/{self.photo.uuid}/content",
            response.content.decode(),
        )

    def test_an_outsider_and_a_photo_outside_the_album_are_404(self):
        outsider = User.objects.create_user(username="outsider", password="p")
        private = make_photo(self.owner, "private.jpg", _at(16))

        self.assertEqual(self.viewer(outsider, self.photo).status_code, 404)
        self.assertEqual(self.viewer(self.bob, private).status_code, 404)

    def test_a_file_no_viewer_fits(self):
        with patch(
            "workspace.photos.ui.views.ViewerRegistry.get_viewer", return_value=None
        ):
            response = self.viewer(self.bob, self.photo)

        self.assertEqual(response.status_code, 400)
