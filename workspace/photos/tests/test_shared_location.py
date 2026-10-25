"""A photo reaches anyone but its owner without the place it was taken."""

import io
import zipfile
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from PIL import ExifTags, Image

from workspace.files.models import File, FileShare, FileShareLink
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.files.services.thumbnails.generation import generate_thumbnail
from workspace.photos.models import AlbumShare
from workspace.photos.services.album_sharing import create_link, share_album
from workspace.photos.services.albums import create_album
from workspace.photos.services.preferences import MODULE, STRIP_LOCATION_ON_SHARE
from workspace.users.services.settings import set_setting

from .images import jpeg_bytes, make_photo

User = get_user_model()

TAKEN = datetime(2024, 7, 14, 12, tzinfo=UTC)
GPS = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (48.0, 51.0, 30.24),
    ExifTags.GPS.GPSLongitudeRef: "E",
    ExifTags.GPS.GPSLongitude: (2.0, 17.0, 40.2),
}


def _body(response):
    if response.streaming:
        return b"".join(response.streaming_content)
    return response.content


def _has_position(data):
    return bool(Image.open(io.BytesIO(data)).getexif().get_ifd(ExifTags.IFD.GPSInfo))


def _zip_members(response):
    archive = zipfile.ZipFile(io.BytesIO(_body(response)))
    return {name: archive.read(name) for name in archive.namelist()}


class SharedLocationTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.original = jpeg_bytes(make="Apple", gps=GPS)
        self.photo = make_photo(
            self.owner,
            "beach.jpg",
            TAKEN,
            data=self.original,
            latitude=48.8584,
            longitude=2.2945,
        )

    def tearDown(self):
        cache.clear()

    def keep_location(self):
        set_setting(self.owner, MODULE, STRIP_LOCATION_ON_SHARE, False)

    def share_with_bob(self, node=None):
        share_file(
            node or self.photo,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.owner,
        )


class FilesEndpointTests(SharedLocationTestCase):
    def content(self, user, action="content", **headers):
        self.client.force_login(user)
        return self.client.get(f"/api/v1/files/{self.photo.uuid}/{action}", **headers)

    def test_the_owner_gets_the_original(self):
        for action in ("content", "download"):
            with self.subTest(action=action):
                self.assertEqual(_body(self.content(self.owner, action)), self.original)

    def test_someone_it_is_shared_with_gets_it_without_the_position(self):
        self.share_with_bob()

        for action in ("content", "download"):
            with self.subTest(action=action):
                served = _body(self.content(self.bob, action))
                self.assertEqual(len(served), len(self.original))
                self.assertFalse(_has_position(served))
                self.assertEqual(
                    Image.open(io.BytesIO(served)).getexif()[ExifTags.Base.Make],
                    "Apple",
                )

    def test_a_rotated_photo_downloads_without_the_position(self):
        # The edited rendering copies the original's EXIF over.
        File.objects.filter(pk=self.photo.pk).update(image_edit={"rotation": 90})
        self.share_with_bob()

        served = _body(self.content(self.bob, "download"))

        self.assertEqual(Image.open(io.BytesIO(served)).size, (48, 64))
        self.assertFalse(_has_position(served))

    def test_a_range_over_the_position_is_blanked_too(self):
        self.share_with_bob()
        whole = _body(self.content(self.bob))
        start = next(
            index
            for index, (a, b) in enumerate(zip(whole, self.original, strict=True))
            if a != b
        )

        response = self.content(self.bob, HTTP_RANGE=f"bytes={start}-{start + 15}")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(_body(response), whole[start : start + 16])

    def test_with_the_preference_off_everyone_gets_the_original(self):
        self.keep_location()
        self.share_with_bob()

        self.assertEqual(_body(self.content(self.bob)), self.original)

    def test_a_folder_zip(self):
        folder = FileService.create_folder(owner=self.owner, name="Trip")
        self.photo.parent = folder
        self.photo.save(update_fields=["parent"])
        self.share_with_bob(folder)
        self.client.force_login(self.bob)

        members = _zip_members(self.client.get(f"/api/v1/files/{folder.uuid}/download"))

        self.assertFalse(_has_position(members["beach.jpg"]))

    def test_a_bulk_zip(self):
        self.share_with_bob()
        self.client.force_login(self.bob)

        members = _zip_members(
            self.client.post(
                "/api/v1/files/bulk-download",
                {"uuids": [str(self.photo.uuid)]},
                content_type="application/json",
            )
        )

        self.assertFalse(_has_position(members["beach.jpg"]))

    def test_a_public_link(self):
        link = FileShareLink.objects.create(file=self.photo, created_by=self.owner)

        for action in ("content", "download"):
            with self.subTest(action=action):
                served = _body(
                    self.client.get(f"/api/v1/files/shared/{link.token}/{action}")
                )
                self.assertEqual(len(served), len(self.original))
                self.assertFalse(_has_position(served))

    def test_a_group_member_gets_the_original(self):
        family = Group.objects.create(name="Family")
        self.owner.groups.add(family)
        self.bob.groups.add(family)
        root = FileService.create_folder(owner=self.owner, name="Family", group=family)
        photo = make_photo(
            self.owner, "home.jpg", TAKEN, parent=root, data=self.original
        )
        self.client.force_login(self.bob)

        served = _body(self.client.get(f"/api/v1/files/{photo.uuid}/content"))

        self.assertEqual(served, self.original)


class AlbumEndpointTests(SharedLocationTestCase):
    def setUp(self):
        super().setUp()
        self.album = create_album(self.owner, "Trip", files=[self.photo])

    def test_a_member_who_may_download(self):
        share_album(
            self.album,
            role=AlbumShare.Role.CONTRIBUTOR,
            acting_user=self.owner,
            user=self.bob,
        )
        self.client.force_login(self.bob)
        base = f"/api/v1/photos/albums/{self.album.uuid}"

        for path in ("content", "download"):
            with self.subTest(path=path):
                served = _body(
                    self.client.get(f"{base}/files/{self.photo.uuid}/{path}")
                )
                self.assertFalse(_has_position(served))
        members = _zip_members(self.client.get(f"{base}/download"))
        self.assertFalse(_has_position(members["beach.jpg"]))

    def test_a_link_that_allows_downloads(self):
        link = create_link(self.album, acting_user=self.owner, allow_download=True)
        base = f"/api/v1/photos/shared/{link.token}"

        for path in ("content", "download"):
            with self.subTest(path=path):
                served = _body(
                    self.client.get(f"{base}/files/{self.photo.uuid}/{path}")
                )
                self.assertFalse(_has_position(served))
        members = _zip_members(self.client.get(f"{base}/download"))
        self.assertFalse(_has_position(members["beach.jpg"]))

    def test_the_owner_downloads_the_original(self):
        self.client.force_login(self.owner)

        served = _body(
            self.client.get(
                f"/api/v1/photos/albums/{self.album.uuid}/files/{self.photo.uuid}/download"
            )
        )

        self.assertEqual(served, self.original)


class PropertiesPanelTests(SharedLocationTestCase):
    def panel(self, user):
        self.client.force_login(user)
        response = self.client.get(
            reverse("files_ui:properties", kwargs={"uuid": self.photo.uuid})
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_owner_sees_the_coordinates(self):
        self.assertIn("48.85840, 2.29450", self.panel(self.owner))

    def test_someone_else_does_not(self):
        self.share_with_bob()

        html = self.panel(self.bob)

        self.assertIn('data-properties-section="photo"', html)
        self.assertNotIn("48.8584", html)
        self.assertNotIn("openstreetmap", html)

    def test_unless_the_owner_keeps_it(self):
        self.keep_location()
        self.share_with_bob()

        self.assertIn("48.85840, 2.29450", self.panel(self.bob))


class ThumbnailTests(SharedLocationTestCase):
    def test_a_thumbnail_carries_no_metadata(self):
        generate_thumbnail(self.photo)
        self.photo.refresh_from_db()
        self.share_with_bob()
        self.client.force_login(self.bob)

        served = _body(self.client.get(f"/api/v1/files/{self.photo.uuid}/thumbnail"))

        self.assertEqual(Image.open(io.BytesIO(served)).format, "WEBP")
        self.assertFalse(Image.open(io.BytesIO(served)).getexif())
