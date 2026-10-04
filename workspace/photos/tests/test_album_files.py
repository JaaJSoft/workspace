"""Album-scoped file endpoints: a member reads the album's photos through the
album, and nothing outside it."""

import io
import zipfile
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services.sharing import share_file
from workspace.files.services.thumbnails.generation import get_thumbnail_path
from workspace.photos.models import AlbumShare
from workspace.photos.services.album_sharing import share_album
from workspace.photos.services.albums import add_items, create_album

from .images import make_photo

User = get_user_model()

API = "/api/v1/photos/albums"


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


def with_thumbnail(file_obj):
    default_storage.save(get_thumbnail_path(file_obj.uuid), ContentFile(b"webp"))
    File.objects.filter(pk=file_obj.pk).update(has_thumbnail=True)
    file_obj.refresh_from_db()
    return file_obj


class AlbumFilesTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.outsider = User.objects.create_user(username="outsider", password="p")
        self.photo = with_thumbnail(make_photo(self.owner, "beach.jpg", _at(14)))
        self.album = create_album(self.owner, "Trip", files=[self.photo])
        share_album(
            self.album,
            role=AlbumShare.Role.VIEWER,
            acting_user=self.owner,
            user=self.bob,
        )
        self.client.force_login(self.bob)

    def tearDown(self):
        cache.clear()

    def file_url(self, file_obj, what, album=None):
        return f"{API}/{(album or self.album).uuid}/files/{file_obj.uuid}/{what}"


class AlbumFileServingTests(AlbumFilesTestCase):
    def test_a_member_reads_a_photo_they_cannot_open_in_files(self):
        self.assertEqual(
            self.client.get(f"/api/v1/files/{self.photo.uuid}/content").status_code, 404
        )

        thumbnail = self.client.get(self.file_url(self.photo, "thumbnail"))
        content = self.client.get(self.file_url(self.photo, "content"))
        download = self.client.get(self.file_url(self.photo, "download"))

        self.assertEqual(thumbnail.status_code, 200)
        self.assertEqual(thumbnail["Content-Type"], "image/webp")
        self.assertEqual(content.status_code, 200)
        self.assertIn("inline", content["Content-Disposition"])
        self.assertEqual(b"".join(content.streaming_content)[:2], b"\xff\xd8")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download["Content-Disposition"])

    def test_content_answers_a_range(self):
        response = self.client.get(
            self.file_url(self.photo, "content"), HTTP_RANGE="bytes=0-1"
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), b"\xff\xd8")

    def test_a_viewer_cannot_download_once_the_album_forbids_it(self):
        self.album.allow_download = False
        self.album.save(update_fields=["allow_download"])

        self.assertEqual(
            self.client.get(self.file_url(self.photo, "download")).status_code, 403
        )
        self.assertEqual(
            self.client.get(f"{API}/{self.album.uuid}/download").status_code, 403
        )
        self.assertEqual(
            self.client.get(self.file_url(self.photo, "content")).status_code, 200
        )

    def test_an_outsider_gets_a_404_everywhere(self):
        self.client.force_login(self.outsider)
        for what in ("thumbnail", "content", "download"):
            with self.subTest(what=what):
                self.assertEqual(
                    self.client.get(self.file_url(self.photo, what)).status_code, 404
                )
        self.assertEqual(
            self.client.get(f"{API}/{self.album.uuid}/download").status_code, 404
        )

    def test_a_file_without_a_thumbnail(self):
        File.objects.filter(pk=self.photo.pk).update(has_thumbnail=False)

        self.assertEqual(
            self.client.get(self.file_url(self.photo, "thumbnail")).status_code, 404
        )

    def test_a_vanished_blob_is_a_404(self):
        File.objects.filter(pk=self.photo.pk).update(
            content="files/users/owner/gone.jpg"
        )

        self.assertEqual(
            self.client.get(self.file_url(self.photo, "content")).status_code, 404
        )


class AlbumFileIdorTests(AlbumFilesTestCase):
    """A membership never reaches a file outside the album."""

    def test_an_owners_photo_outside_the_album(self):
        elsewhere = with_thumbnail(make_photo(self.owner, "private.jpg", _at(15)))
        for what in ("thumbnail", "content", "download"):
            with self.subTest(what=what):
                self.assertEqual(
                    self.client.get(self.file_url(elsewhere, what)).status_code, 404
                )

    def test_a_photo_of_another_album(self):
        other = create_album(self.owner, "Other")
        secret = with_thumbnail(make_photo(self.owner, "secret.jpg", _at(16)))
        add_items(other, [secret], added_by=self.owner)

        self.assertEqual(
            self.client.get(self.file_url(secret, "content")).status_code, 404
        )
        self.assertEqual(
            self.client.get(self.file_url(secret, "content", album=other)).status_code,
            404,
        )

    def test_an_item_nobody_in_reach_may_pass_on(self):
        stranger = User.objects.create_user(username="stranger", password="p")
        theirs = with_thumbnail(make_photo(stranger, "theirs.jpg", _at(17)))
        share_file(
            theirs, target_user=self.owner, permission="ro", acting_user=stranger
        )
        add_items(self.album, [theirs], added_by=self.owner)

        self.assertEqual(
            self.client.get(self.file_url(theirs, "content")).status_code, 404
        )
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(self.file_url(theirs, "content")).status_code, 200
        )

    def test_a_trashed_photo(self):
        File.objects.filter(pk=self.photo.pk).update(deleted_at=_at(20))

        self.assertEqual(
            self.client.get(self.file_url(self.photo, "thumbnail")).status_code, 404
        )


class AlbumArchiveTests(AlbumFilesTestCase):
    def setUp(self):
        super().setUp()
        # Same name, two contributors: the archive keeps both.
        share_album(
            self.album,
            role=AlbumShare.Role.CONTRIBUTOR,
            acting_user=self.owner,
            user=self.bob,
        )
        self.second = make_photo(self.bob, "beach.jpg", _at(13))
        add_items(self.album, [self.second], added_by=self.bob)

    def archive(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))

    def test_the_whole_album(self):
        response = self.client.get(f"{API}/{self.album.uuid}/download")

        self.assertIn('filename="Trip.zip"', response["Content-Disposition"])
        self.assertEqual(
            sorted(self.archive(response).namelist()), ["beach (2).jpg", "beach.jpg"]
        )

    def test_a_selection(self):
        response = self.client.post(
            f"{API}/{self.album.uuid}/download",
            {"files": [str(self.second.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(self.archive(response).namelist(), ["beach.jpg"])

    def test_a_selection_reaching_outside_the_album(self):
        elsewhere = make_photo(self.owner, "private.jpg", _at(15))

        response = self.client.post(
            f"{API}/{self.album.uuid}/download",
            {"files": [str(self.photo.uuid), str(elsewhere.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)

    def test_a_malformed_selection(self):
        for body in ({"files": ["nope"]}, {"files": []}, {"files": ["x"] * 2001}):
            with self.subTest(size=len(body["files"])):
                response = self.client.post(
                    f"{API}/{self.album.uuid}/download",
                    body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)

    def test_a_selection_is_refused_without_download(self):
        self.album.allow_download = False
        self.album.save(update_fields=["allow_download"])
        share_album(
            self.album,
            role=AlbumShare.Role.VIEWER,
            acting_user=self.owner,
            user=self.bob,
        )

        response = self.client.post(
            f"{API}/{self.album.uuid}/download",
            {"files": [str(self.photo.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_an_outsider_selection_is_a_404(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            f"{API}/{self.album.uuid}/download",
            {"files": [str(self.photo.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)

    def test_a_name_without_extension_is_numbered_too(self):
        File.objects.filter(pk__in=[self.photo.pk, self.second.pk]).update(name="scan")

        names = self.archive(
            self.client.get(f"{API}/{self.album.uuid}/download")
        ).namelist()

        self.assertEqual(sorted(names), ["scan", "scan (2)"])
