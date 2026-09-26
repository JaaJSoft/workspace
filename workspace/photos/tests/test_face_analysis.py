from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from workspace.common.vectors import nearest
from workspace.common.vectors.encoding import from_bytes
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Face, FaceAnalysis, FaceCluster
from workspace.photos.services.face_analysis import (
    analyze_faces,
    forget_faces,
    is_face_candidate,
    pending_faces_qs,
)

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, faces_png, upload

User = get_user_model()


@faces_on
class AnalyzeFacesTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)

    def test_stores_each_face_with_its_box_crop_and_vector(self):
        photo = upload(
            self.user,
            "pair.png",
            faces_png((ALICE, (40, 50, 100)), (BOB, (240, 100, 80))),
        )

        faces = analyze_faces(photo)

        self.assertEqual(len(faces), 2)
        alice = min(faces, key=lambda face: face.box_x)
        self.assertAlmostEqual(alice.box_x, 40 / 400)
        self.assertAlmostEqual(alice.box_y, 50 / 300)
        self.assertAlmostEqual(alice.box_width, 100 / 400)
        self.assertAlmostEqual(alice.box_height, 100 / 300)
        self.assertEqual(len(alice.landmarks), 5)
        self.assertEqual(alice.owner_id, self.user.pk)
        for face in faces:
            face.refresh_from_db()
            self.assertTrue(default_storage.exists(face.crop))
            self.assertIsNotNone(from_bytes(face.embedding, FACE_EMBEDDINGS.dims))
        hits = nearest(
            FACE_EMBEDDINGS,
            from_bytes(alice.embedding, FACE_EMBEDDINGS.dims),
            partition=self.user.pk,
            k=2,
        )
        self.assertEqual(hits[0][0], alice.pk)
        analysis = FaceAnalysis.objects.get(file=photo)
        self.assertEqual(analysis.face_count, 2)
        self.assertEqual(analysis.backend, "fake")
        self.assertEqual(analysis.content_hash, photo.content_hash)

    def test_a_photo_without_faces_is_recorded_as_analyzed(self):
        photo = upload(self.user, "landscape.png", faces_png())

        self.assertEqual(analyze_faces(photo), [])

        self.assertEqual(FaceAnalysis.objects.get(file=photo).face_count, 0)
        self.assertNotIn(photo, pending_faces_qs())

    def test_an_undecodable_photo_is_recorded_as_analyzed(self):
        photo = upload(self.user, "broken.png", faces_png())
        File.objects.filter(pk=photo.pk).update(type="png")
        photo.content.save("broken.png", ContentFile(b"not a picture"), save=False)

        self.assertEqual(analyze_faces(photo), [])
        self.assertTrue(FaceAnalysis.objects.filter(file=photo).exists())

    @override_settings(PHOTOS_FACES_MIN_SIZE=50)
    def test_drops_faces_too_small_to_tell_apart(self):
        photo = upload(
            self.user,
            "crowd.png",
            faces_png((ALICE, (40, 50, 100)), (BOB, (240, 100, 30))),
        )

        faces = analyze_faces(photo)

        self.assertEqual(len(faces), 1)

    @override_settings(PHOTOS_FACES_MAX_PER_PHOTO=1)
    def test_keeps_the_configured_number_of_faces_at_most(self):
        photo = upload(
            self.user,
            "pair.png",
            faces_png((ALICE, (40, 50, 100)), (BOB, (240, 100, 80))),
        )

        self.assertEqual(len(analyze_faces(photo)), 1)

    def test_reanalysis_keeps_what_the_user_decided(self):
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        (face,) = analyze_faces(photo)
        cluster = FaceCluster.objects.create(owner=self.user)
        Face.objects.filter(pk=face.pk).update(
            cluster=cluster, assignment=Face.Assignment.CONFIRMED
        )
        FaceCluster.objects.filter(pk=cluster.pk).update(cover=face)
        old_crop = Face.objects.get(pk=face.pk).crop

        with self.captureOnCommitCallbacks(execute=True):
            (new_face,) = analyze_faces(photo)

        new_face.refresh_from_db()
        self.assertNotEqual(new_face.pk, face.pk)
        self.assertEqual(new_face.cluster_id, cluster.pk)
        self.assertEqual(new_face.assignment, Face.Assignment.CONFIRMED)
        cluster.refresh_from_db()
        self.assertEqual(cluster.cover_id, new_face.pk)
        self.assertFalse(default_storage.exists(old_crop))
        self.assertFalse(Face.objects.filter(pk=face.pk).exists())


@faces_on
class CandidateTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_not_a_candidate_until_the_user_opts_in(self):
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))

        self.assertFalse(is_face_candidate(photo))
        self.assertIsNone(analyze_faces(photo))
        self.assertNotIn(photo, pending_faces_qs())

        opt_in(self.user)
        self.assertTrue(is_face_candidate(photo))
        self.assertIn(photo, pending_faces_qs())

    @override_settings(PHOTOS_FACES_ENABLED=False)
    def test_not_a_candidate_on_an_instance_without_the_feature(self):
        opt_in(self.user)
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))

        self.assertIsNone(analyze_faces(photo))
        self.assertFalse(Face.objects.exists())

    def test_group_photos_are_not_read(self):
        opt_in(self.user)
        group = Group.objects.create(name="Team")
        self.user.groups.add(group)
        folder = FileService.create_folder(owner=self.user, name="Team", group=group)
        photo = upload(
            self.user, "team.png", faces_png((ALICE, (40, 50, 100))), parent=folder
        )

        self.assertFalse(is_face_candidate(photo))
        self.assertNotIn(photo, pending_faces_qs())

    def test_new_bytes_make_a_photo_pending_again(self):
        opt_in(self.user)
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        analyze_faces(photo)
        self.assertNotIn(photo, pending_faces_qs())

        File.objects.filter(pk=photo.pk).update(content_hash="0" * 64)

        self.assertIn(photo, pending_faces_qs())

    def test_another_backend_makes_a_photo_pending_again(self):
        opt_in(self.user)
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        analyze_faces(photo)

        FaceAnalysis.objects.filter(file=photo).update(backend="yunet_sface")

        self.assertIn(photo, pending_faces_qs())

    def test_forgetting_a_photo_drops_its_faces_and_crops(self):
        opt_in(self.user)
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))
        (face,) = analyze_faces(photo)
        crop = Face.objects.get(pk=face.pk).crop

        with self.captureOnCommitCallbacks(execute=True):
            forget_faces(photo)

        self.assertFalse(Face.objects.filter(file=photo).exists())
        self.assertFalse(FaceAnalysis.objects.filter(file=photo).exists())
        self.assertFalse(default_storage.exists(crop))
        self.assertEqual(
            nearest(
                FACE_EMBEDDINGS,
                [1.0] * FACE_EMBEDDINGS.dims,
                partition=self.user.pk,
                k=5,
            ),
            [],
        )


@faces_on
class AnalysisFailureTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))

    def test_a_failing_backend_stores_nothing_and_leaves_the_photo_pending(self):
        with (
            patch(
                "workspace.photos.services.detection.fake.FakeFaceBackend.detect",
                side_effect=RuntimeError("model crashed"),
            ),
            self.assertLogs("workspace.photos.services.face_analysis", "ERROR"),
        ):
            self.assertIsNone(analyze_faces(self.photo))

        self.assertFalse(FaceAnalysis.objects.exists())
        self.assertIn(self.photo, pending_faces_qs())

    @override_settings(PHOTOS_FACE_BACKEND="yunet-sface")
    def test_a_misconfigured_backend_leaves_the_photo_pending(self):
        with self.assertLogs("workspace.photos.services.face_analysis", "ERROR"):
            self.assertIsNone(analyze_faces(self.photo))

        self.assertFalse(FaceAnalysis.objects.exists())

    def test_a_photo_replaced_while_it_was_read_keeps_nothing(self):
        written = []
        real_save = default_storage.save

        def save_then_replace(name, content):
            written.append(real_save(name, content))
            File.objects.filter(pk=self.photo.pk).update(content_hash="f" * 64)
            return written[-1]

        with patch.object(default_storage, "save", side_effect=save_then_replace):
            self.assertIsNone(analyze_faces(self.photo))

        self.assertFalse(Face.objects.exists())
        self.assertFalse(FaceAnalysis.objects.exists())
        self.assertTrue(written)
        self.assertFalse(any(default_storage.exists(name) for name in written))

    @override_settings(PHOTOS_FACES_MAX_FILE_BYTES=10)
    def test_an_oversized_original_is_recorded_without_reading_it(self):
        with patch(
            "workspace.photos.services.detection.fake.FakeFaceBackend.detect"
        ) as detect:
            self.assertEqual(analyze_faces(self.photo), [])

        detect.assert_not_called()
        self.assertEqual(FaceAnalysis.objects.get(file=self.photo).face_count, 0)

    def test_a_blob_that_cannot_be_opened_is_tried_again_later(self):
        with (
            patch.object(type(self.photo.content), "open", side_effect=OSError("gone")),
            self.assertLogs("workspace.photos.services.face_analysis", "WARNING"),
        ):
            self.assertIsNone(analyze_faces(self.photo))

        self.assertIn(self.photo, pending_faces_qs())


class SharpnessTests(TestCase):
    def test_a_blurred_face_scores_lower_than_a_sharp_one(self):
        from workspace.photos.services.detection.yunet_sface import YuNetSFaceBackend

        rng = np.random.default_rng(0)
        sharp = rng.integers(0, 255, (112, 112, 3), dtype=np.uint8)
        flat = np.full((112, 112, 3), 128, dtype=np.uint8)
        backend = YuNetSFaceBackend()

        self.assertEqual(backend.sharpness(sharp), 1.0)
        self.assertEqual(backend.sharpness(flat), 0.0)
