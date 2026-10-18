from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from workspace.common.vectors.encoding import from_bytes
from workspace.files.services import FileService
from workspace.photos.models import Appearance, AppearanceAnalysis
from workspace.photos.services import appearances
from workspace.photos.services.appearances import (
    analyze_appearances,
    is_appearance_candidate,
    pending_appearances_qs,
    torso_box,
)
from workspace.photos.services.detection.nanodet import DetectedPerson
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.face_analysis import analyze_faces, forget_faces
from workspace.photos.services.face_purge import purge_owner_faces

from .appearances import BLUE, RED, appearances_on, contact, torso_png
from .faces import FacesTestMixin, opt_in
from .images import faces_png, upload

User = get_user_model()

# A person 200 px wide standing in an 800 x 600 photo: a torso 200 x 240.
STANDING = (100.0, 50.0, 200.0, 500.0)
# The same 100 px wide: a torso under MIN_TORSO_WIDTH.
FAR = (500.0, 50.0, 100.0, 250.0)


def people(*boxes):
    """Patch the person detector to find *boxes*."""
    return patch.object(
        appearances,
        "detect_people",
        return_value=[DetectedPerson(box=box, score=0.9) for box in boxes],
    )


class TorsoBoxTests(TestCase):
    def test_the_torso_is_under_the_head_down_to_the_waist(self):
        self.assertEqual(torso_box(STANDING, 800, 600), (100, 130, 300, 370))

    def test_a_person_cut_off_at_the_shoulders_shows_no_torso(self):
        self.assertIsNone(torso_box((100.0, 50.0, 200.0, 150.0), 800, 600))

    def test_a_narrow_torso_shows_too_little(self):
        self.assertIsNone(torso_box(FAR, 800, 600))

    def test_the_torso_stops_at_the_edge_of_the_photo(self):
        self.assertEqual(
            torso_box((100.0, 300.0, 200.0, 500.0), 800, 600), (100, 380, 300, 600)
        )


@appearances_on
class AnalyzeAppearancesTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)

    def test_stores_each_person_with_box_crop_and_vector(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))

        with people(STANDING):
            rows = analyze_appearances(photo)

        (row,) = rows
        row.refresh_from_db()
        self.assertAlmostEqual(row.box_x, 100 / 800)
        self.assertAlmostEqual(row.box_height, 500 / 600)
        self.assertTrue(default_storage.exists(row.crop))
        vector = from_bytes(row.embedding, FakeEmbeddingBackend.dims)
        # The fake model embeds the mean colour: the crop is the red torso.
        self.assertGreater(vector[0], 0)
        self.assertLess(vector[2], 0)
        analysis = AppearanceAnalysis.objects.get(file=photo)
        self.assertEqual(analysis.appearance_count, 1)
        self.assertEqual(analysis.model, "fake")
        self.assertNotIn(photo, pending_appearances_qs())

    def test_people_too_small_to_show_their_clothes_are_left_out(self):
        photo = upload(self.user, "far.png", torso_png((FAR, BLUE)))

        with people(FAR):
            self.assertEqual(analyze_appearances(photo), [])

        self.assertEqual(AppearanceAnalysis.objects.get(file=photo).appearance_count, 0)

    def test_a_photo_with_nobody_is_recorded_as_analyzed(self):
        photo = upload(self.user, "empty.png", torso_png())

        with people():
            self.assertEqual(analyze_appearances(photo), [])

        self.assertNotIn(photo, pending_appearances_qs())

    def test_a_new_analysis_keeps_who_the_user_said_it_is(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))
        with people(STANDING):
            (row,) = analyze_appearances(photo)
        lea = contact(self.user, "Léa")
        Appearance.objects.filter(pk=row.pk).update(person=lea)

        with people((102.0, 52.0, 200.0, 500.0)):
            (again,) = analyze_appearances(photo)

        self.assertNotEqual(again.pk, row.pk)
        self.assertEqual(again.person_id, lea.pk)
        self.assertFalse(Appearance.objects.filter(pk=row.pk).exists())

    def test_nothing_is_read_for_a_user_who_did_not_turn_faces_on(self):
        bob = User.objects.create_user(username="bob", password="p")
        photo = upload(bob, "pair.png", torso_png((STANDING, RED)))

        self.assertFalse(is_appearance_candidate(photo))
        self.assertIsNone(analyze_appearances(photo))

    def test_nothing_is_read_from_a_group_folder(self):
        group = Group.objects.create(name="Team")
        self.user.groups.add(group)
        folder = FileService.create_folder(owner=self.user, name="Team", group=group)
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)), parent=folder)

        self.assertFalse(is_appearance_candidate(photo))

    @override_settings(PHOTOS_APPEARANCES_ENABLED=False)
    def test_nothing_is_read_where_the_instance_turned_it_off(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))

        self.assertFalse(is_appearance_candidate(photo))

    def test_nothing_is_read_with_a_backend_not_calibrated_for_torsos(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))

        with patch.object(FakeEmbeddingBackend, "appearance_max_distance", None):
            self.assertFalse(is_appearance_candidate(photo))

    def test_a_photo_read_for_faces_is_queued_for_its_people(self):
        photo = upload(self.user, "faces.png", faces_png())

        with (
            patch("workspace.photos.tasks.analyze_photo_appearances.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            analyze_faces(photo)

        delay.assert_called_once_with(str(photo.pk))

    def test_a_photo_leaving_the_library_takes_its_people_along(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))
        with people(STANDING):
            (row,) = analyze_appearances(photo)

        with self.captureOnCommitCallbacks(execute=True):
            forget_faces(photo)

        self.assertFalse(Appearance.objects.filter(file=photo).exists())
        self.assertFalse(AppearanceAnalysis.objects.filter(file=photo).exists())
        self.assertFalse(default_storage.exists(row.crop))

    def test_turning_faces_off_purges_the_people_found_by_their_body(self):
        photo = upload(self.user, "pair.png", torso_png((STANDING, RED)))
        with people(STANDING):
            (row,) = analyze_appearances(photo)

        with self.captureOnCommitCallbacks(execute=True):
            purge_owner_faces(self.user.pk)

        self.assertFalse(Appearance.objects.filter(owner=self.user).exists())
        self.assertFalse(AppearanceAnalysis.objects.filter(owner=self.user).exists())
        self.assertFalse(default_storage.exists(row.crop))


class NanoDetDecodingTests(TestCase):
    """The person detector's outputs turned into boxes, without its weights."""

    def _run(self, image_size, anchor, bins, score=0.9):
        from workspace.photos.services.detection import nanodet

        outputs = []
        for stride in (8, 16, 32):
            count = (416 // stride) ** 2
            classes = np.zeros((1, count, 80), dtype=np.float32)
            distributions = np.full((1, count, 32), -10.0, dtype=np.float32)
            if stride == 8:
                classes[0, anchor, 0] = score
                for side, peak in enumerate(bins):
                    distributions[0, anchor, side * 8 + peak] = 10.0
            outputs += [classes, distributions]

        class Session:
            def get_inputs(self):
                return [type("Input", (), {"name": "input.1"})()]

            def run(self, names, feeds):
                assert feeds["input.1"].shape == (1, 3, 416, 416)
                return outputs

        image = np.zeros((*image_size, 3), dtype=np.uint8)
        with patch.object(nanodet.runtime, "session", return_value=Session()):
            return nanodet.detect_people(image)

    def test_a_confident_anchor_becomes_a_box_in_the_photo_pixels(self):
        # Anchor 54 of the 52 x 52 grid: column 2, row 1, centred on (19.5, 11.5).
        (person,) = self._run((416, 416), anchor=54, bins=(2, 1, 3, 4))

        x, y, w, h = person.box
        self.assertAlmostEqual(x, 19.5 - 16, places=3)
        self.assertAlmostEqual(y, 11.5 - 8, places=3)
        self.assertAlmostEqual(w, 16 + 24, places=3)
        self.assertAlmostEqual(h, 8 + 32, places=3)
        self.assertAlmostEqual(person.score, 0.9, places=5)

    def test_the_box_is_scaled_back_to_a_larger_photo(self):
        (person,) = self._run((832, 832), anchor=54, bins=(2, 1, 3, 4))

        self.assertAlmostEqual(person.box[2], 2 * 40, places=3)

    def test_an_unsure_anchor_is_nobody(self):
        self.assertEqual(
            self._run((416, 416), anchor=54, bins=(2, 1, 3, 4), score=0.2), []
        )
