"""The data migration that retypes HEIF and AVIF photos detected as MP4."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File, MediaInfo, ProcessingFailure
from workspace.files.services import FileService
from workspace.files.tests.rasters import avif_bytes, heic_bytes
from workspace.files.tests.videos import clip_bytes

migration = importlib.import_module(
    "workspace.files.migrations.0060_retype_isobmff_images"
)

User = get_user_model()


class _AppsAtThisMigration:
    """The live app registry, answering for ThumbnailFailure under the name a
    later migration gave it: its historical table no longer exists."""

    def get_model(self, app_label, model_name):
        if (app_label, model_name) == ("files", "ThumbnailFailure"):
            model_name = "ProcessingFailure"
        return apps.get_model(app_label, model_name)


class RetypeIsoMediaImagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retype", password="x")

    def _stored_as_mp4(self, name, data):
        """A file as the old detection left it: typed as an MP4 video."""
        file_obj = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )
        File.objects.filter(pk=file_obj.pk).update(
            type="mp4", category="video", mime_type="video/mp4"
        )
        return file_obj

    def _migrate(self):
        migration.retype_images(_AppsAtThisMigration(), schema_editor_stub())

    def test_heic_and_avif_become_images(self):
        heic = self._stored_as_mp4("IMG_0001.HEIC", heic_bytes())
        avif = self._stored_as_mp4("photo.avif", avif_bytes())

        self._migrate()

        self.assertEqual(
            File.objects.filter(pk=heic.pk)
            .values_list("type", "category", "mime_type")
            .get(),
            ("heif", "image", "image/heic"),
        )
        self.assertEqual(
            File.objects.filter(pk=avif.pk)
            .values_list("type", "category", "mime_type")
            .get(),
            ("avif", "image", "image/avif"),
        )

    def test_an_avif_in_the_video_group_moves_to_images(self):
        avif = self._stored_as_mp4("photo.avif", avif_bytes())
        File.objects.filter(pk=avif.pk).update(type="avif")

        self._migrate()

        self.assertEqual(File.objects.get(pk=avif.pk).category, "image")

    def test_a_video_named_heic_stays_a_video(self):
        video = self._stored_as_mp4("renamed.heic", clip_bytes("clip_hevc.mp4"))

        self._migrate()

        self.assertEqual(File.objects.get(pk=video.pk).type, "mp4")

    def test_video_probe_and_thumbnail_failures_are_dropped(self):
        heic = self._stored_as_mp4("IMG_0002.heic", heic_bytes())
        MediaInfo.objects.create(file=heic, duration=0.0, probed_at=timezone.now())
        ProcessingFailure.objects.create(
            file=heic,
            processor="thumbnails",
            attempts=3,
            last_attempt_at=timezone.now(),
        )

        self._migrate()

        self.assertFalse(MediaInfo.objects.filter(file=heic).exists())
        self.assertFalse(ProcessingFailure.objects.filter(file=heic).exists())
