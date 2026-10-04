"""Public album links: managing them, and what a token reaches."""

import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services.sharing import share_file
from workspace.files.services.thumbnails.generation import get_thumbnail_path
from workspace.photos.models import Album, AlbumLink, AlbumShare
from workspace.photos.services.album_links import access_token_for, has_access
from workspace.photos.services.album_sharing import create_link, share_album
from workspace.photos.services.albums import add_items, create_album

from .images import make_photo

User = get_user_model()

API = "/api/v1/photos/albums"
PUBLIC = "/api/v1/photos/shared"


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


def with_thumbnail(file_obj):
    default_storage.save(get_thumbnail_path(file_obj.uuid), ContentFile(b"webp"))
    File.objects.filter(pk=file_obj.pk).update(has_thumbnail=True)
    file_obj.refresh_from_db()
    return file_obj


class LinkTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.photos = [
            with_thumbnail(make_photo(self.owner, f"day{day}.jpg", _at(day)))
            for day in (14, 15, 16)
        ]
        self.album = create_album(self.owner, "Trip", files=self.photos)

    def tearDown(self):
        cache.clear()


class LinkManagementApiTests(LinkTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = f"{API}/{self.album.uuid}/links"

    def test_create_list_and_revoke(self):
        response = self.client.post(
            self.url,
            {"password": "s3cret", "allow_download": True},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        link = AlbumLink.objects.get()
        self.assertTrue(data["url"].endswith(f"/photos/shared/{link.token}"))
        self.assertTrue(data["has_password"])
        self.assertTrue(data["allow_download"])
        self.assertNotEqual(link.password, "s3cret")
        self.assertEqual(len(self.client.get(self.url).json()), 1)

        revoked = self.client.delete(f"{self.url}/{link.uuid}")
        self.assertEqual(revoked.status_code, 204)
        self.assertFalse(AlbumLink.objects.exists())
        self.assertEqual(self.client.delete(f"{self.url}/{link.uuid}").status_code, 404)

    def test_links_do_not_download_unless_asked(self):
        data = self.client.post(self.url, {}, content_type="application/json").json()

        self.assertFalse(data["allow_download"])
        self.assertFalse(data["has_password"])
        self.assertIsNone(data["expires_at"])

    def test_an_expiry_in_the_past_is_refused(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()

        response = self.client.post(
            self.url, {"expires_at": past}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)

    def test_a_contributor_cannot_manage_links(self):
        share_album(
            self.album,
            role=AlbumShare.Role.CONTRIBUTOR,
            acting_user=self.owner,
            user=self.bob,
        )
        self.client.force_login(self.bob)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(self.url, {}, content_type="application/json").status_code,
            403,
        )

    def test_a_link_of_another_album_is_not_revoked(self):
        other = create_album(self.owner, "Other")
        link = create_link(other, acting_user=self.owner)

        self.assertEqual(self.client.delete(f"{self.url}/{link.uuid}").status_code, 404)
        self.assertTrue(AlbumLink.objects.filter(pk=link.pk).exists())


class PublicLinkApiTests(LinkTestCase):
    def setUp(self):
        super().setUp()
        self.link = create_link(self.album, acting_user=self.owner)

    def url(self, file_obj, what, link=None, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{PUBLIC}/{(link or self.link).token}/files/{file_obj.uuid}/{what}" + (
            f"?{query}" if query else ""
        )

    def test_a_visitor_sees_thumbnails_and_photos(self):
        thumbnail = self.client.get(self.url(self.photos[0], "thumbnail"))
        content = self.client.get(self.url(self.photos[0], "content"))

        self.assertEqual(thumbnail.status_code, 200)
        self.assertIn("public", thumbnail["Cache-Control"])
        self.assertEqual(content.status_code, 200)

    def test_downloads_follow_the_link_setting(self):
        self.assertEqual(
            self.client.get(self.url(self.photos[0], "download")).status_code, 403
        )
        self.assertEqual(
            self.client.get(f"{PUBLIC}/{self.link.token}/download").status_code, 403
        )

        self.link.allow_download = True
        self.link.save(update_fields=["allow_download"])

        self.assertEqual(
            self.client.get(self.url(self.photos[0], "download")).status_code, 200
        )
        archive = self.client.get(f"{PUBLIC}/{self.link.token}/download")
        names = zipfile.ZipFile(
            io.BytesIO(b"".join(archive.streaming_content))
        ).namelist()
        self.assertEqual(sorted(names), ["day14.jpg", "day15.jpg", "day16.jpg"])

    def test_an_unknown_token(self):
        self.assertEqual(
            self.client.get(
                f"{PUBLIC}/nope/files/{self.photos[0].uuid}/content"
            ).status_code,
            404,
        )

    def test_an_expired_link(self):
        AlbumLink.objects.filter(pk=self.link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        self.assertEqual(
            self.client.get(self.url(self.photos[0], "content")).status_code, 410
        )
        self.assertEqual(
            self.client.post(
                f"{PUBLIC}/{self.link.token}/verify", {"password": "x"}
            ).status_code,
            410,
        )

    def test_a_revoked_link(self):
        self.link.delete()

        self.assertEqual(
            self.client.get(self.url(self.photos[0], "thumbnail")).status_code, 404
        )


class PublicLinkIdorTests(LinkTestCase):
    """A token reaches the album's items, and nothing else."""

    def setUp(self):
        super().setUp()
        self.link = create_link(self.album, acting_user=self.owner, allow_download=True)

    def get(self, file_obj, what):
        return self.client.get(
            f"{PUBLIC}/{self.link.token}/files/{file_obj.uuid}/{what}"
        )

    def test_the_owners_photo_outside_the_album(self):
        private = with_thumbnail(make_photo(self.owner, "private.jpg", _at(20)))
        for what in ("thumbnail", "content", "download"):
            with self.subTest(what=what):
                self.assertEqual(self.get(private, what).status_code, 404)

    def test_a_photo_of_another_album_of_the_same_owner(self):
        other = create_album(self.owner, "Other")
        secret = make_photo(self.owner, "secret.jpg", _at(21))
        add_items(other, [secret], added_by=self.owner)

        self.assertEqual(self.get(secret, "content").status_code, 404)

    def test_an_item_shared_with_the_owner_alone(self):
        stranger = User.objects.create_user(username="stranger", password="p")
        theirs = make_photo(stranger, "theirs.jpg", _at(22))
        share_file(
            theirs, target_user=self.owner, permission="ro", acting_user=stranger
        )
        add_items(self.album, [theirs], added_by=self.owner)

        self.assertEqual(self.get(theirs, "content").status_code, 404)
        archive = self.client.get(f"{PUBLIC}/{self.link.token}/download")
        names = zipfile.ZipFile(
            io.BytesIO(b"".join(archive.streaming_content))
        ).namelist()
        self.assertNotIn("theirs.jpg", names)

    def test_a_trashed_photo(self):
        File.objects.filter(pk=self.photos[0].pk).update(deleted_at=timezone.now())

        self.assertEqual(self.get(self.photos[0], "content").status_code, 404)


class PublicLinkPasswordTests(LinkTestCase):
    def setUp(self):
        super().setUp()
        self.link = create_link(self.album, acting_user=self.owner, password="s3cret")
        self.content_url = (
            f"{PUBLIC}/{self.link.token}/files/{self.photos[0].uuid}/content"
        )

    def verify(self, password):
        return self.client.post(
            f"{PUBLIC}/{self.link.token}/verify",
            {"password": password},
            content_type="application/json",
        )

    def test_the_right_password_earns_an_access_token(self):
        self.assertEqual(self.client.get(self.content_url).status_code, 403)
        self.assertEqual(self.verify("wrong").status_code, 403)

        token = self.verify("s3cret").json()["access_token"]

        self.assertEqual(
            self.client.get(f"{self.content_url}?access_token={token}").status_code, 200
        )

    def test_a_token_of_another_link_opens_nothing(self):
        other = create_link(self.album, acting_user=self.owner, password="other")

        self.assertFalse(has_access(self.link, access_token_for(other)))
        self.assertFalse(has_access(self.link, "garbage"))
        self.assertTrue(has_access(self.link, access_token_for(self.link)))

    def test_a_stale_token_opens_nothing(self):
        token = access_token_for(self.link)

        with self.settings():
            from unittest.mock import patch

            later = timezone.now() + timedelta(hours=2)
            with patch("django.core.signing.time.time", return_value=later.timestamp()):
                self.assertFalse(has_access(self.link, token))

    def test_verify_is_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.verify("wrong").status_code, 403)

        self.assertEqual(self.verify("s3cret").status_code, 429)

    def test_a_link_without_password_has_nothing_to_verify(self):
        open_link = create_link(self.album, acting_user=self.owner)

        response = self.client.post(
            f"{PUBLIC}/{open_link.token}/verify", {"password": "x"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(f"{PUBLIC}/nope/verify").status_code, 404)


class PublicPageTests(LinkTestCase):
    def setUp(self):
        super().setUp()
        self.link = create_link(self.album, acting_user=self.owner)
        self.page = f"/photos/shared/{self.link.token}"

    def test_the_grid_without_an_account(self):
        response = self.client.get(self.page)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Trip", html)
        self.assertIn("3 photos", html)
        for photo in self.photos:
            self.assertIn(
                f"{PUBLIC}/{self.link.token}/files/{photo.uuid}/thumbnail", html
            )
        self.assertNotIn("Download all", html)
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 1)
        self.assertIsNotNone(self.link.last_accessed_at)

    def test_download_all_when_allowed(self):
        self.link.allow_download = True
        self.link.save(update_fields=["allow_download"])

        html = self.client.get(self.page).content.decode()

        self.assertIn(f"{PUBLIC}/{self.link.token}/download", html)

    def test_a_photo_in_the_viewer_between_its_neighbours(self):
        middle = self.photos[1]

        response = self.client.get(f"{self.page}?photo={middle.uuid}")

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{PUBLIC}/{self.link.token}/files/{middle.uuid}/content", html)
        self.assertIn(f"photo={self.photos[2].uuid}", html)
        self.assertIn(f"photo={self.photos[0].uuid}", html)
        self.assertIn("2 / 3", html)

    def test_a_swap_renders_the_content_alone_and_is_not_counted(self):
        response = self.client.get(
            f"{self.page}?photo={self.photos[0].uuid}", HTTP_X_ALPINE_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", response.content.decode())
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 0)

    def test_a_photo_outside_the_album_is_a_404(self):
        private = make_photo(self.owner, "private.jpg", _at(20))

        self.assertEqual(
            self.client.get(f"{self.page}?photo={private.uuid}").status_code, 404
        )
        self.assertEqual(self.client.get(f"{self.page}?photo=nope").status_code, 404)

    def test_a_manual_album_reads_in_its_own_order(self):
        Album.objects.filter(pk=self.album.pk).update(sort_mode=Album.SortMode.MANUAL)

        html = self.client.get(
            f"{self.page}?photo={self.photos[0].uuid}"
        ).content.decode()

        self.assertIn("1 / 3", html)
        self.assertIn(f"photo={self.photos[1].uuid}", html)

    def test_the_password_card(self):
        self.link.password = make_password("s3cret")
        self.link.save(update_fields=["password"])

        html = self.client.get(self.page).content.decode()

        self.assertIn("Protected link", html)
        self.assertIn(f"{PUBLIC}/{self.link.token}/verify", html)
        self.assertNotIn("/thumbnail", html)
        token = access_token_for(self.link)
        unlocked = self.client.get(f"{self.page}?access_token={token}").content.decode()
        self.assertIn(f"thumbnail?{urlencode({'access_token': token})}", unlocked)

    def test_an_expired_link(self):
        AlbumLink.objects.filter(pk=self.link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        html = self.client.get(self.page).content.decode()

        self.assertIn("Link expired", html)
        self.assertNotIn("Trip", html)

    def test_an_unknown_link(self):
        self.assertEqual(self.client.get("/photos/shared/nope").status_code, 404)

    def test_the_next_page(self):
        self.link.password = make_password("s3cret")
        self.link.save(update_fields=["password"])
        token = access_token_for(self.link)
        more = [
            make_photo(self.owner, f"more{i}.jpg", _at(1) - timedelta(days=i + 1))
            for i in range(60)
        ]
        add_items(self.album, more, added_by=self.owner)

        html = self.client.get(f"{self.page}?access_token={token}").content.decode()
        match = re.search(r"timelineSentinel\('([^']+)'\)", html)
        self.assertIsNotNone(match)
        # Written through |escapejs, as a JS string literal.
        next_url = json.loads(f'"{match.group(1)}"')
        self.assertIn("access_token=", next_url)

        page = self.client.get(next_url)

        self.assertEqual(page.status_code, 200)
        self.assertIn("data-shared-photo", page.content.decode())

    def test_the_next_page_wants_the_password_too(self):
        self.link.password = make_password("s3cret")
        self.link.save(update_fields=["password"])
        url = f"{self.page}/timeline?cursor=x"

        self.assertEqual(self.client.get(url).status_code, 404)
        token = access_token_for(self.link)
        self.assertEqual(
            self.client.get(f"{url}&access_token={token}").status_code, 400
        )
        self.assertEqual(
            self.client.get("/photos/shared/nope/timeline").status_code, 404
        )
