"""Thumbnail renditions: every size is generated, served and deleted together."""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template import Context, Template
from django.test import SimpleTestCase
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File, FileShareLink
from workspace.files.services import FileService
from workspace.files.services.thumbnails.generation import (
    THUMBNAIL_SIZES,
    delete_thumbnail,
    generate_thumbnail,
    get_thumbnail_path,
    thumbnail_variant_path,
)

User = get_user_model()


def _webp(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="WEBP")
    return buf.getvalue()


def _stored_size(path):
    with default_storage.open(path, "rb") as fh:
        img = Image.open(io.BytesIO(fh.read()))
        return img.size


def _drain(resp):
    # Closes the FileResponse handle, which Windows needs before a delete.
    if hasattr(resp, "streaming_content"):
        return b"".join(resp.streaming_content)
    return resp.content


class ThumbnailSizesGenerationTests(IsolatedMediaRootMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="sizes", password="p")

    def _photo(self):
        buf = io.BytesIO()
        Image.new("RGB", (2000, 1500), (10, 120, 200)).save(buf, format="JPEG")
        f = FileService.create_file(
            owner=self.user,
            name="big.jpg",
            content=ContentFile(buf.getvalue(), name="big.jpg"),
            mime_type="image/jpeg",
        )
        f.type = "jpeg"
        f.save(update_fields=["type"])
        return f

    def test_generation_writes_every_size_keeping_the_aspect_ratio(self):
        f = self._photo()

        self.assertTrue(generate_thumbnail(f))

        self.assertEqual(_stored_size(get_thumbnail_path(f.uuid, 128)), (128, 96))
        self.assertEqual(_stored_size(get_thumbnail_path(f.uuid, 256)), (256, 192))
        self.assertEqual(_stored_size(get_thumbnail_path(f.uuid)), (512, 384))

    def test_delete_removes_every_size(self):
        f = self._photo()
        generate_thumbnail(f)

        delete_thumbnail(f.uuid)

        for size in THUMBNAIL_SIZES:
            self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid, size)))

    def test_a_missing_variant_is_derived_from_the_full_size_thumbnail(self):
        # A thumbnail generated before the variants existed has only 512px.
        f = File.objects.create(
            owner=self.user, name="old.jpg", node_type=File.NodeType.FILE
        )
        default_storage.save(get_thumbnail_path(f.uuid), ContentFile(_webp((512, 300))))

        path = thumbnail_variant_path(f.uuid, 128)

        self.assertEqual(path, get_thumbnail_path(f.uuid, 128))
        self.assertEqual(_stored_size(path), (128, 75))

    def test_no_variant_from_an_unreadable_full_size_thumbnail(self):
        f = File.objects.create(
            owner=self.user, name="bad.jpg", node_type=File.NodeType.FILE
        )
        default_storage.save(get_thumbnail_path(f.uuid), ContentFile(b"not an image"))

        with self.assertLogs(
            "workspace.files.services.thumbnails.generation", "WARNING"
        ):
            self.assertIsNone(thumbnail_variant_path(f.uuid, 128))

    def test_no_variant_without_a_full_size_thumbnail(self):
        f = File.objects.create(
            owner=self.user, name="none.jpg", node_type=File.NodeType.FILE
        )

        self.assertIsNone(thumbnail_variant_path(f.uuid, 256))
        self.assertFalse(default_storage.exists(get_thumbnail_path(f.uuid, 256)))

    def test_losing_a_concurrent_derivation_leaves_no_duplicate_behind(self):
        f = File.objects.create(
            owner=self.user, name="race.jpg", node_type=File.NodeType.FILE
        )
        default_storage.save(get_thumbnail_path(f.uuid), ContentFile(_webp((512, 512))))
        variant = get_thumbnail_path(f.uuid, 256)
        # Another request stored the variant after this one checked for it.
        default_storage.save(variant, ContentFile(_webp((256, 256))))
        real_exists = default_storage.exists
        unseen = {variant}

        # Only the first check misses it: the storage's own name search
        # must still see the file, or it never finds a free name.
        def exists(path):
            if path in unseen:
                unseen.discard(path)
                return False
            return real_exists(path)

        with mock.patch.object(default_storage, "exists", side_effect=exists):
            self.assertEqual(thumbnail_variant_path(f.uuid, 256), variant)

        _, names = default_storage.listdir("thumbnails")
        self.assertEqual(
            sorted(names), sorted([f"{f.uuid}.webp", f"{f.uuid}_256.webp"])
        )


class ThumbnailSizeEndpointTests(IsolatedMediaRootMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="sizes-api", password="p")
        self.client.force_authenticate(self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="photo.jpg",
            node_type=File.NodeType.FILE,
            mime_type="image/jpeg",
            has_thumbnail=True,
        )
        default_storage.save(
            get_thumbnail_path(self.file.uuid), ContentFile(_webp((512, 512)))
        )
        self.url = f"/api/v1/files/{self.file.uuid}/thumbnail"

    def test_size_serves_that_rendition(self):
        resp = self.client.get(self.url, {"size": "128"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Image.open(io.BytesIO(_drain(resp))).size, (128, 128))

    def test_no_size_serves_the_full_thumbnail(self):
        resp = self.client.get(self.url)

        self.assertEqual(Image.open(io.BytesIO(_drain(resp))).size, (512, 512))

    def test_an_unsupported_size_is_a_bad_request(self):
        for value in ("300", "big", "-1"):
            resp = self.client.get(self.url, {"size": value})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, value)

    def test_share_link_serves_a_rendition(self):
        link = FileShareLink.objects.create(
            file=self.file, created_by=self.user, mode=FileShareLink.Mode.READ
        )
        self.client.force_authenticate(None)

        resp = self.client.get(
            f"/api/v1/files/shared/{link.token}/thumbnail", {"size": "256"}
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Image.open(io.BytesIO(_drain(resp))).size, (256, 256))

    def test_share_link_refuses_an_unsupported_size(self):
        link = FileShareLink.objects.create(
            file=self.file, created_by=self.user, mode=FileShareLink.Mode.READ
        )
        self.client.force_authenticate(None)

        resp = self.client.get(
            f"/api/v1/files/shared/{link.token}/thumbnail", {"size": "64"}
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ThumbnailSrcsetFilterTests(SimpleTestCase):
    def _render(self, url):
        return Template("{% load file_filters %}{{ url|thumbnail_srcset }}").render(
            Context({"url": url})
        )

    def test_offers_every_size(self):
        self.assertEqual(
            self._render("/api/v1/files/u/thumbnail"),
            "/api/v1/files/u/thumbnail?size=128 128w, "
            "/api/v1/files/u/thumbnail?size=256 256w, "
            "/api/v1/files/u/thumbnail 512w",
        )

    def test_appends_to_an_existing_query_string(self):
        self.assertEqual(
            self._render("/t/thumbnail?file=f"),
            "/t/thumbnail?file=f&amp;size=128 128w, "
            "/t/thumbnail?file=f&amp;size=256 256w, "
            "/t/thumbnail?file=f 512w",
        )
