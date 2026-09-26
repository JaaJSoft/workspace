"""Turning faces on and off, moves, and the nightly safety net."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import default_storage
from django.test import RequestFactory, TestCase, override_settings

from workspace.common.vectors import nearest
from workspace.core.services.admin_dashboard import face_backend_health_card
from workspace.files.models import File, FileEvent
from workspace.files.services import FileService
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Face, FaceAnalysis, FaceCluster
from workspace.photos.services.face_analysis import analyze_faces
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
from workspace.photos.services.handlers import follow_moved_photo
from workspace.photos.tasks import cluster_all_faces, purge_faces, queue_owner_faces
from workspace.users.services.settings import delete_setting, set_setting

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, faces_png, upload

User = get_user_model()


@faces_on
class TurningFacesOffTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        for name, faces in (
            ("alice-1.png", [(ALICE, (40, 50, 100))]),
            ("alice-2.png", [(ALICE, (200, 80, 90))]),
            ("pair.png", [(ALICE, (20, 50, 100)), (BOB, (250, 50, 100))]),
        ):
            analyze_faces(upload(self.user, name, faces_png(*faces)))
        cluster_owner(self.user.pk)
        self.crops = list(Face.objects.values_list("crop", flat=True))

    def _assert_nothing_left(self):
        self.assertFalse(Face.objects.filter(owner=self.user).exists())
        self.assertFalse(FaceCluster.objects.filter(owner=self.user).exists())
        self.assertFalse(FaceAnalysis.objects.filter(owner=self.user).exists())
        for crop in self.crops:
            self.assertFalse(default_storage.exists(crop), crop)
        self.assertEqual(
            nearest(
                FACE_EMBEDDINGS,
                [1.0] * FACE_EMBEDDINGS.dims,
                partition=self.user.pk,
                k=10,
            ),
            [],
        )

    def test_turning_the_setting_off_removes_every_face_cluster_and_crop(self):
        self.assertTrue(FaceCluster.objects.filter(owner=self.user).exists())
        self.assertTrue(all(default_storage.exists(crop) for crop in self.crops))

        with (
            patch("workspace.photos.tasks.purge_faces.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            set_setting(self.user, MODULE, FACES_ENABLED, False)
        delay.assert_called_once_with(self.user.pk)
        with self.captureOnCommitCallbacks(execute=True):
            purge_faces(self.user.pk)

        self._assert_nothing_left()

    def test_deleting_the_setting_purges_too(self):
        with (
            patch("workspace.photos.tasks.purge_faces.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            delete_setting(self.user, MODULE, FACES_ENABLED)

        delay.assert_called_once_with(self.user.pk)

    def test_a_purge_queued_before_the_user_turned_it_back_on_keeps_the_data(self):
        purge_faces(self.user.pk)

        self.assertTrue(Face.objects.filter(owner=self.user).exists())

    def test_the_nightly_pass_purges_a_user_who_is_no_longer_opted_in(self):
        # As if the purge task had been lost.
        with patch("workspace.photos.tasks.purge_faces.delay"):
            set_setting(self.user, MODULE, FACES_ENABLED, False)

        with self.captureOnCommitCallbacks(execute=True):
            cluster_all_faces()

        self._assert_nothing_left()

    def test_other_users_keep_their_faces(self):
        other = User.objects.create_user(username="bob", password="p")
        opt_in(other)
        analyze_faces(upload(other, "bob.png", faces_png((BOB, (40, 50, 100)))))

        with patch("workspace.photos.tasks.purge_faces.delay"):
            set_setting(self.user, MODULE, FACES_ENABLED, False)
        purge_faces(self.user.pk)

        self.assertEqual(Face.objects.filter(owner=other).count(), 1)


@faces_on
class TurningFacesOnTests(FacesTestMixin, TestCase):
    def test_turning_the_setting_on_queues_the_library(self):
        user = User.objects.create_user(username="alice", password="p")
        upload(user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        upload(user, "bob.png", faces_png((BOB, (40, 50, 100))))

        with (
            patch("workspace.photos.tasks.queue_owner_faces.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            opt_in(user)
        delay.assert_called_once_with(user.pk)

        with patch("workspace.photos.tasks.analyze_photo_faces.apply_async") as send:
            self.assertEqual(queue_owner_faces(user.pk), 2)
        self.assertEqual(send.call_count, 2)


@faces_on
class MovedPhotoTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        analyze_faces(self.photo)
        self.crop = Face.objects.get().crop

    def _moved(self):
        self.photo.refresh_from_db()
        return FileEvent(file=self.photo, action=FileEvent.Action.MOVED)

    def test_a_photo_moved_to_a_group_folder_loses_its_faces(self):
        group = Group.objects.create(name="Team")
        self.user.groups.add(group)
        folder = FileService.create_folder(owner=self.user, name="Team", group=group)
        File.objects.filter(pk=self.photo.pk).update(parent=folder, group=group)

        with self.captureOnCommitCallbacks(execute=True):
            follow_moved_photo(self._moved())

        self.assertFalse(Face.objects.exists())
        self.assertFalse(FaceAnalysis.objects.exists())
        self.assertFalse(default_storage.exists(self.crop))

    def test_a_photo_given_to_another_owner_is_queued_for_them(self):
        other = User.objects.create_user(username="bob", password="p")
        opt_in(other)
        File.objects.filter(pk=self.photo.pk).update(owner=other)

        with (
            patch("workspace.photos.tasks.analyze_photo_faces.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            follow_moved_photo(self._moved())

        self.assertFalse(Face.objects.filter(owner=self.user).exists())
        delay.assert_called_once_with(str(self.photo.pk))

    def test_a_photo_moved_between_personal_folders_keeps_its_faces(self):
        folder = FileService.create_folder(owner=self.user, name="Holidays")
        File.objects.filter(pk=self.photo.pk).update(parent=folder)

        follow_moved_photo(self._moved())

        self.assertEqual(Face.objects.count(), 1)


class FaceBackendHealthCardTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    @override_settings(PHOTOS_FACES_ENABLED=False)
    def test_no_card_when_the_instance_has_faces_off(self):
        self.assertIsNone(face_backend_health_card(self.request))

    @override_settings(PHOTOS_FACES_ENABLED=True, PHOTOS_FACE_BACKEND="yunet-sface")
    def test_a_misconfigured_backend_shows_as_an_error(self):
        card = face_backend_health_card(self.request)

        self.assertEqual(card["tone"], "danger")
        self.assertIn("unknown face backend 'yunet-sface'", card["value"])

    @override_settings(PHOTOS_FACES_ENABLED=True, PHOTOS_FACE_BACKEND="fake")
    def test_a_working_backend_shows_as_healthy(self):
        card = face_backend_health_card(self.request)

        self.assertEqual(card["tone"], "success")

    @override_settings(PHOTOS_FACES_ENABLED=True, PHOTOS_FACE_BACKEND="yunet-sface")
    def test_a_misconfigured_backend_fails_the_analysis_instead_of_skipping_it(self):
        from workspace.photos.services.detection.registry import (
            BackendMisconfigured,
            get_face_backend,
        )

        with self.assertRaises(BackendMisconfigured):
            get_face_backend().detect(None)
