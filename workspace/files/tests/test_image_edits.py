"""Rotation and crop of an image file, its bytes untouched."""

import hashlib
import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django.utils.html import escapejs
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.actions import ActionRegistry
from workspace.files.models import File, FileShare
from workspace.files.services import FilePermission, FileService
from workspace.files.services._storage_ops import copy_node
from workspace.files.services.image_edits import (
    apply_edit,
    edit_version,
    edited_size,
    normalize_edit,
    rotate_edit,
    versioned_thumbnail_url,
)
from workspace.files.services.thumbnails.generation import (
    generate_thumbnail,
    get_thumbnail_path,
)
from workspace.files.tasks import redraw_edited_thumbnail
from workspace.files.templatetags.file_filters import thumbnail_url

User = get_user_model()

RED = (220, 20, 20)
BLUE = (20, 20, 220)


def _halves(size=(200, 100)):
    """An image whose left half is red and right half blue."""
    img = Image.new("RGB", size, BLUE)
    img.paste(RED, (0, 0, size[0] // 2, size[1]))
    return img


def _jpeg(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _stored_size(path):
    with default_storage.open(path, "rb") as fh:
        return Image.open(io.BytesIO(fh.read())).size


def _close(color, expected, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(color, expected, strict=True))


class NormalizeEditTests(SimpleTestCase):
    def test_an_edit_that_changes_nothing_is_none(self):
        self.assertIsNone(normalize_edit(0, None))
        self.assertIsNone(normalize_edit(0, {"x": 0, "y": 0, "width": 1, "height": 1}))

    def test_rotation_and_crop_are_kept_rounded(self):
        edit = normalize_edit(90, {"x": 0.123456, "y": 0, "width": 0.5, "height": 0.75})
        self.assertEqual(
            edit,
            {
                "rotation": 90,
                "crop": {"x": 0.1235, "y": 0, "width": 0.5, "height": 0.75},
            },
        )

    def test_a_rotation_off_the_quarter_turns_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_edit(45, None)

    def test_a_crop_outside_the_image_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_edit(0, {"x": 0.6, "y": 0, "width": 0.5, "height": 1})

    def test_a_crop_too_small_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_edit(0, {"x": 0, "y": 0, "width": 0.001, "height": 1})


class RotateEditTests(SimpleTestCase):
    def test_a_clockwise_turn_carries_the_crop_with_the_image(self):
        # The left strip of the image is the top strip once turned clockwise.
        edit = {"crop": {"x": 0, "y": 0, "width": 0.25, "height": 1}}
        self.assertEqual(
            rotate_edit(edit, 1),
            {"rotation": 90, "crop": {"x": 0, "y": 0, "width": 1, "height": 0.25}},
        )

    def test_four_turns_come_back_to_the_same_edit(self):
        edit = {"crop": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}}
        turned = edit
        for _ in range(4):
            turned = rotate_edit(turned, 1)
        self.assertEqual(turned, edit)

    def test_turning_back_undoes_a_turn(self):
        self.assertIsNone(rotate_edit(rotate_edit(None, 1), -1))

    def test_edited_size_swaps_on_a_quarter_turn_and_follows_the_crop(self):
        self.assertEqual(edited_size(4000, 3000, {"rotation": 90}), (3000, 4000))
        self.assertEqual(
            edited_size(
                4000,
                3000,
                {"rotation": 0, "crop": {"x": 0, "y": 0, "width": 0.5, "height": 0.5}},
            ),
            (2000, 1500),
        )


class ApplyEditTests(SimpleTestCase):
    def test_a_quarter_turn_is_clockwise(self):
        img = apply_edit(_halves(), {"rotation": 90})
        self.assertEqual(img.size, (100, 200))
        # The left (red) half is now on top.
        self.assertEqual(img.getpixel((50, 20)), RED)
        self.assertEqual(img.getpixel((50, 180)), BLUE)

    def test_the_crop_is_taken_on_the_turned_image(self):
        edit = {
            "rotation": 90,
            "crop": {"x": 0, "y": 0.5, "width": 1, "height": 0.5},
        }
        img = apply_edit(_halves(), edit)
        self.assertEqual(img.size, (100, 100))
        self.assertEqual(img.getpixel((50, 50)), BLUE)


class ImageEditTestMixin:
    def _photo(self, owner, size=(2000, 1000)):
        f = FileService.create_file(
            owner=owner,
            name="photo.jpg",
            content=ContentFile(_jpeg(_halves(size)), name="photo.jpg"),
            mime_type="image/jpeg",
        )
        f.type = "jpeg"
        f.save(update_fields=["type"])
        return f

    @staticmethod
    def _blob_digest(f):
        with f.content.open("rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()


class ThumbnailEditTests(ImageEditTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="thumbs", password="p")

    def test_the_thumbnail_shows_the_rotation(self):
        f = self._photo(self.user)
        f.image_edit = {"rotation": 90}

        self.assertTrue(generate_thumbnail(f))

        self.assertEqual(_stored_size(get_thumbnail_path(f.uuid)), (256, 512))

    def test_a_crop_is_decoded_large_enough_to_fill_the_thumbnail(self):
        f = self._photo(self.user, size=(4000, 2000))
        f.image_edit = {"crop": {"x": 0.5, "y": 0, "width": 0.25, "height": 0.25}}

        self.assertTrue(generate_thumbnail(f))

        # 1000 x 500 pixels of the original: a reduced decode would leave
        # fewer than the 512 the thumbnail spans.
        self.assertEqual(_stored_size(get_thumbnail_path(f.uuid)), (512, 256))
        with default_storage.open(get_thumbnail_path(f.uuid), "rb") as fh:
            thumb = Image.open(io.BytesIO(fh.read())).convert("RGB")
        self.assertTrue(_close(thumb.getpixel((256, 128)), BLUE))


class ImageEditApiTests(ImageEditTestMixin, APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.other = User.objects.create_user(username="other", password="p")
        self.file = self._photo(self.owner)
        self.client.force_authenticate(self.owner)

    def tearDown(self):
        cache.clear()

    def _url(self, suffix=""):
        return f"/api/v1/files/{self.file.uuid}/image-edit{suffix}"

    def _put(self, body):
        return self.client.put(self._url(), body, format="json")

    def _put_and_redraw(self, body):
        """PUT *body*, then run the redraw task it queued on commit."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            resp = self._put(body)
        self.assertEqual(len(callbacks), 1)
        return resp

    def test_put_stores_the_edit_and_queues_the_thumbnail(self):
        digest = self._blob_digest(self.file)

        with self.captureOnCommitCallbacks() as callbacks:
            resp = self._put(
                {
                    "rotation": 90,
                    "crop": {"x": 0, "y": 0, "width": 1, "height": 0.5},
                }
            )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Nothing is drawn in the request: the thumbnail is pending until the
        # task queued on commit runs.
        self.assertFalse(resp.data["thumbnail_ready"])
        self.file.refresh_from_db()
        self.assertFalse(self.file.has_thumbnail)
        with patch("workspace.files.sse_provider.push_file_event") as push:
            for callback in callbacks:
                callback()
        push.assert_called_once()
        self.assertEqual(push.call_args.args[1], "processing_settled")
        expected = {
            "rotation": 90,
            "crop": {"x": 0, "y": 0, "width": 1, "height": 0.5},
        }
        self.assertEqual(resp.data["image_edit"], expected)
        self.assertEqual(resp.data["version"], edit_version(expected))
        self.file.refresh_from_db()
        self.assertEqual(self.file.image_edit, expected)
        self.assertTrue(self.file.has_thumbnail)
        # Turned to 1000 x 2000, then the top half kept: a square.
        self.assertEqual(_stored_size(get_thumbnail_path(self.file.uuid)), (512, 512))
        self.assertEqual(self._blob_digest(self.file), digest)

    def test_delete_reverts_to_the_original(self):
        self._put_and_redraw({"rotation": 270})

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.delete(self._url())

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["image_edit"])
        self.assertEqual(resp.data["version"], "")
        self.file.refresh_from_db()
        self.assertIsNone(self.file.image_edit)
        self.assertEqual(_stored_size(get_thumbnail_path(self.file.uuid)), (512, 256))

    def test_an_edit_that_changes_nothing_is_the_original(self):
        self._put({"rotation": 90})

        resp = self._put(
            {"rotation": 0, "crop": {"x": 0, "y": 0, "width": 1, "height": 1}}
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.image_edit)

    def test_rotate_turns_the_current_edit(self):
        self._put({"crop": {"x": 0, "y": 0, "width": 0.5, "height": 1}})

        resp = self.client.post(
            self._url("/rotate"), {"quarter_turns": 1}, format="json"
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.data["image_edit"],
            {"rotation": 90, "crop": {"x": 0, "y": 0, "width": 1, "height": 0.5}},
        )

    def test_an_invalid_edit_is_refused(self):
        for body in (
            {"rotation": 45},
            {"crop": {"x": 0.8, "y": 0, "width": 0.5, "height": 1}},
            {"crop": {"x": 0, "y": 0, "width": 0, "height": 1}},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self._put(body).status_code, status.HTTP_400_BAD_REQUEST
                )
        self.assertEqual(
            self.client.post(
                self._url("/rotate"), {"quarter_turns": 2}, format="json"
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_a_read_only_share_cannot_edit(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.other,
            permission=FileShare.Permission.READ_ONLY,
        )
        self.client.force_authenticate(self.other)

        self.assertEqual(
            self._put({"rotation": 90}).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.delete(self._url()).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_read_write_share_can_edit(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.other,
            permission=FileShare.Permission.READ_WRITE,
        )
        self.client.force_authenticate(self.other)

        self.assertEqual(self._put({"rotation": 90}).status_code, status.HTTP_200_OK)

    def test_an_outsider_gets_a_404(self):
        self.client.force_authenticate(self.other)

        self.assertEqual(
            self._put({"rotation": 90}).status_code, status.HTTP_404_NOT_FOUND
        )
        self.file.refresh_from_db()
        self.assertIsNone(self.file.image_edit)

    def test_a_file_locked_by_someone_else_is_refused(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.other,
            permission=FileShare.Permission.READ_WRITE,
        )
        now = timezone.now()
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.other,
            locked_at=now,
            lock_expires_at=now + timedelta(minutes=5),
        )

        self.assertEqual(
            self._put({"rotation": 90}).status_code, status.HTTP_403_FORBIDDEN
        )
        self.file.refresh_from_db()
        self.assertIsNone(self.file.image_edit)

    def test_a_document_cannot_be_edited(self):
        doc = FileService.create_file(
            owner=self.owner,
            name="notes.txt",
            content=ContentFile(b"hello", name="notes.txt"),
            mime_type="text/plain",
        )

        resp = self.client.put(
            f"/api/v1/files/{doc.uuid}/image-edit", {"rotation": 90}, format="json"
        )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_new_content_drops_the_edit(self):
        self._put({"rotation": 90})
        self.file.refresh_from_db()

        FileService.update_content(
            self.file, ContentFile(_jpeg(_halves((300, 300))), name="photo.jpg")
        )

        self.file.refresh_from_db()
        self.assertIsNone(self.file.image_edit)

    def test_a_copy_keeps_the_edit_on_its_own_row(self):
        self._put({"rotation": 180})
        self.file.refresh_from_db()

        copied = copy_node(self.file, None, self.owner)

        self.assertEqual(copied.image_edit, {"rotation": 180})
        self.assertNotEqual(copied.pk, self.file.pk)


class EditImageActionTests(ImageEditTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="acts", password="p")
        self.file = self._photo(self.user)

    def _action(self, file_obj, permission=FilePermission.MANAGE):
        actions = ActionRegistry.get_available_actions(
            self.user, file_obj, permission=permission
        )
        return next((a for a in actions if a["id"] == "edit_image"), None)

    def test_offered_on_an_image_with_its_edit_state(self):
        self.assertEqual(self._action(self.file)["state"], {"is_edited": False})
        self.file.image_edit = {"rotation": 90}
        self.assertEqual(self._action(self.file)["state"], {"is_edited": True})

    def test_needs_write_permission(self):
        self.assertIsNotNone(self._action(self.file, FilePermission.WRITE))
        self.assertIsNone(self._action(self.file, FilePermission.VIEW))

    def test_not_offered_on_a_trashed_file(self):
        self.file.deleted_at = timezone.now()
        self.assertIsNone(self._action(self.file))


class VersionedThumbnailUrlTests(SimpleTestCase):
    def test_an_edited_image_has_a_url_of_its_own(self):
        f = File(has_thumbnail=True, image_edit={"rotation": 90})

        url = thumbnail_url(f)

        self.assertTrue(url.endswith(f"/thumbnail?v={edit_version(f.image_edit)}"))

    def test_an_unedited_image_keeps_the_plain_url(self):
        f = File(has_thumbnail=True)

        self.assertTrue(thumbnail_url(f).endswith("/thumbnail"))

    def test_the_version_joins_an_existing_query(self):
        f = File(image_edit={"rotation": 90})

        self.assertIn(
            "?access_token=x&v=", versioned_thumbnail_url("/t?access_token=x", f)
        )


class ImageViewerEditTests(ImageEditTestMixin, TestCase):
    """The viewer offers rotate and crop where the registry does, and shows
    the stored edit."""

    def setUp(self):
        self.owner = User.objects.create_user(username="viewer", password="p")
        self.reader = User.objects.create_user(username="reader", password="p")
        self.file = self._photo(self.owner)
        File.objects.filter(pk=self.file.pk).update(image_edit={"rotation": 90})

    def _html(self, user, query=""):
        self.client.force_login(user)
        return self.client.get(f"/files/view/{self.file.uuid}{query}").content.decode()

    def test_an_editor_gets_the_edit_button_and_the_stored_edit(self):
        html = self._html(self.owner)

        self.assertIn('data-testid="image-edit-start"', html)
        self.assertIn(
            '<script id="image-viewer-edit-data" type="application/json">'
            '{"rotation": 90}</script>',
            html,
        )
        self.assertIn("const openInEditMode = false;", html)

    def test_an_edited_image_downloads_through_the_rendering_endpoint(self):
        html = self._html(self.owner)

        url = escapejs(f"/api/v1/files/{self.file.uuid}/download")
        self.assertIn(f"const editedDownloadUrl = '{url}';", html)

    def test_edit_in_the_query_opens_in_edit_mode(self):
        self.assertIn("const openInEditMode = true;", self._html(self.owner, "?edit=1"))

    def test_a_reader_sees_the_edit_but_cannot_change_it(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.reader,
            permission=FileShare.Permission.READ_ONLY,
        )

        html = self._html(self.reader, "?edit=1")

        self.assertNotIn('data-testid="image-edit-start"', html)
        self.assertIn("const openInEditMode = false;", html)
        self.assertIn('{"rotation": 90}', html)


class RedrawTaskTests(ImageEditTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="redraw", password="p")
        self.file = self._photo(self.user)

    def test_a_file_whose_thumbnail_is_drawn_is_left_alone(self):
        File.objects.filter(pk=self.file.pk).update(has_thumbnail=True)

        with patch("workspace.files.sse_provider.push_file_event") as push:
            result = redraw_edited_thumbnail.apply(args=[str(self.file.pk)]).get()

        self.assertEqual(result, {"status": "skipped"})
        push.assert_not_called()

    def test_a_pending_thumbnail_is_drawn_with_the_edit(self):
        File.objects.filter(pk=self.file.pk).update(
            has_thumbnail=False, image_edit={"rotation": 90}
        )

        with patch("workspace.files.sse_provider.push_file_event"):
            result = redraw_edited_thumbnail.apply(args=[str(self.file.pk)]).get()

        self.assertEqual(result, {"status": "ok"})
        self.file.refresh_from_db()
        self.assertTrue(self.file.has_thumbnail)
        self.assertEqual(_stored_size(get_thumbnail_path(self.file.uuid)), (256, 512))


class EditedDownloadTests(ImageEditTestMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dl", password="p")
        self.file = self._photo(self.user)
        self.client.force_authenticate(self.user)

    def _download(self):
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/download")
        body = (
            b"".join(resp.streaming_content)
            if hasattr(resp, "streaming_content")
            else resp.content
        )
        return resp, body

    def test_an_unedited_image_downloads_its_original_bytes(self):
        resp, body = self._download()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(hashlib.sha256(body).hexdigest(), self._blob_digest(self.file))

    def test_an_edited_image_downloads_as_it_is_shown(self):
        File.objects.filter(pk=self.file.pk).update(
            image_edit={
                "rotation": 90,
                "crop": {"x": 0, "y": 0, "width": 1, "height": 0.5},
            }
        )

        resp, body = self._download()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertIn('filename="photo.jpg"', resp["Content-Disposition"])
        img = Image.open(io.BytesIO(body))
        # 2000 x 1000 turned is 1000 x 2000; the top half is the red one.
        self.assertEqual(img.size, (1000, 1000))
        self.assertTrue(_close(img.convert("RGB").getpixel((500, 500)), RED))

    def test_the_metadata_comes_along_with_the_orientation_reset(self):
        source = _halves((200, 100))
        exif = source.getexif()
        exif[0x0112] = 6  # stored sideways: shown turned clockwise
        exif[0x010F] = "Camera maker"
        buf = io.BytesIO()
        source.save(buf, format="JPEG", exif=exif.tobytes())
        FileService.update_content(self.file, ContentFile(buf.getvalue(), name="p.jpg"))
        File.objects.filter(pk=self.file.pk).update(image_edit={"rotation": 90})

        resp, body = self._download()

        img = Image.open(io.BytesIO(body))
        # EXIF turns it once, the edit a second time: upside down, landscape.
        self.assertEqual(img.size, (200, 100))
        self.assertEqual(img.getexif()[0x0112], 1)
        self.assertEqual(img.getexif()[0x010F], "Camera maker")
        self.assertTrue(_close(img.convert("RGB").getpixel((150, 50)), RED))

    def test_a_gif_loses_its_animation_and_comes_out_as_png(self):
        from workspace.files.services.image_edits import render_edited

        buf = io.BytesIO()
        _halves().save(buf, format="GIF")
        FileService.update_content(
            self.file, ContentFile(buf.getvalue(), name="anim.gif")
        )
        self.file.refresh_from_db()
        self.file.name = "anim.gif"
        self.file.image_edit = {"rotation": 180}

        data, content_type, name = render_edited(self.file)

        self.assertEqual((content_type, name), ("image/png", "anim.png"))
        self.assertEqual(Image.open(io.BytesIO(data)).format, "PNG")
