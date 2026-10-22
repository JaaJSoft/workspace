from datetime import UTC

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.vectors.encoding import normalize, to_bytes
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Appearance, Face
from workspace.photos.services import appearance_suggestions
from workspace.photos.services.appearance_suggestions import (
    suggest_around,
    suggest_around_files,
    suggest_owner,
)
from workspace.photos.services.face_grouping import refresh_clusters
from workspace.users.services.settings import set_setting

from .appearances import (
    BLUE,
    DARK_RED,
    RED,
    appearances_on,
    contact,
    named_face,
    photo_at,
    seen,
)
from .faces import FacesTestMixin, opt_in

User = get_user_model()


def face_vector(*components):
    vector = np.zeros(FACE_EMBEDDINGS.dims)
    vector[: len(components)] = components
    return to_bytes(normalize(vector, FACE_EMBEDDINGS.dims))


@appearances_on
class SuggestAppearancesTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.lea = contact(self.user, "Léa")
        self.noah = contact(self.user, "Noah")

    def anchor(self, person, colour, hours=0.0):
        """*person*, named by their face, wearing *colour* on a photo taken
        *hours* after noon."""
        photo = photo_at(self.user, f"{person.display_name}-{hours}.jpg", hours)
        named_face(self.user, photo, person)
        return seen(self.user, photo, colour)

    def suggestion(self, row):
        row.refresh_from_db()
        return row.suggested_person_id

    def test_the_same_clothes_nearby_suggest_the_person(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        self.assertEqual(suggest_owner(self.user.pk), 1)

        self.assertEqual(self.suggestion(back), self.lea.pk)
        back.refresh_from_db()
        self.assertAlmostEqual(back.suggestion_distance, 0.0, places=4)

    def test_other_clothes_suggest_nobody(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), DARK_RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_the_same_clothes_another_day_suggest_nobody(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 30), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_the_same_clothes_later_that_day_suggest_the_person(self):
        self.anchor(self.lea, RED, hours=-11)
        back = seen(self.user, photo_at(self.user, "back.jpg", 11), RED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_past_midnight_the_window_still_holds(self):
        self.anchor(self.lea, RED, hours=11)
        back = seen(self.user, photo_at(self.user, "back.jpg", 14), RED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_the_next_morning_is_another_day(self):
        self.anchor(self.lea, RED, hours=10)
        back = seen(self.user, photo_at(self.user, "back.jpg", 19), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_the_day_is_the_owners(self):
        # 01:00 and 22:00 UTC: one day in UTC, two days in Tokyo (10:00 and
        # 07:00 the next morning).
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")
        self.anchor(self.lea, RED, hours=-11)
        back = seen(self.user, photo_at(self.user, "back.jpg", 10), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_around_a_photo_its_whole_day_is_asked_about_again(self):
        morning = seen(self.user, photo_at(self.user, "morning.jpg", -11), RED)
        lea = self.anchor(self.lea, RED, hours=10)

        suggest_around(lea.file)

        self.assertEqual(self.suggestion(morning), self.lea.pk)

    def test_two_people_dressed_alike_suggest_neither(self):
        self.anchor(self.lea, RED)
        self.anchor(self.noah, RED, hours=2)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_someone_unnamed_dressed_alike_is_a_rival_too(self):
        self.anchor(self.lea, RED)
        stranger = photo_at(self.user, "stranger.jpg", 2)
        named_face(self.user, stranger, None)
        seen(self.user, stranger, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_someone_whose_face_is_in_no_group_is_a_rival_too(self):
        self.anchor(self.lea, RED)
        stranger = photo_at(self.user, "stranger.jpg", 2)
        face = named_face(self.user, stranger, None)
        Face.objects.filter(pk=face.pk).update(cluster=None)
        seen(self.user, stranger, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_someone_with_a_face_too_blurred_to_group_is_asked_about(self):
        self.anchor(self.lea, RED)
        blurred = photo_at(self.user, "blurred.jpg", 1)
        face = named_face(self.user, blurred, None)
        Face.objects.filter(pk=face.pk).update(cluster=None, quality=0.2)
        back = seen(self.user, blurred, RED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_dressed_like_an_unnamed_group_suggests_nobody(self):
        stranger = photo_at(self.user, "stranger.jpg")
        named_face(self.user, stranger, None)
        seen(self.user, stranger, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_a_person_is_suggested_once_per_photo_for_the_nearest(self):
        self.anchor(self.lea, RED)
        group = photo_at(self.user, "group.jpg", 1)
        near = seen(self.user, group, RED)
        further = seen(self.user, group, (240, 0, 0), box=(0.6, 0.1, 0.3, 0.8))

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(near), self.lea.pk)
        self.assertIsNone(self.suggestion(further))

    def test_two_clusters_of_one_person_are_no_rivals(self):
        self.anchor(self.lea, RED)
        other_look = photo_at(self.user, "lea-2.jpg", 2)
        named_face(self.user, other_look, self.lea)
        seen(self.user, other_look, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_a_person_whose_face_names_them_is_asked_about_nothing(self):
        anchor = self.anchor(self.lea, RED)
        self.anchor(self.lea, RED, hours=1)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(anchor))

    def test_someone_already_in_the_photo_is_never_suggested(self):
        self.anchor(self.lea, RED)
        group = photo_at(self.user, "group.jpg", 1)
        named_face(self.user, group, self.lea, box=(0.65, 0.12, 0.1, 0.12))
        seen(self.user, group, BLUE, box=(0.6, 0.1, 0.3, 0.8))
        back = seen(self.user, group, RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_a_face_off_the_head_of_the_only_person_left_is_theirs(self):
        # Léa crouching: her face sits below the top of her box.
        crouching = photo_at(self.user, "crouching.jpg")
        named_face(self.user, crouching, self.lea, box=(0.15, 0.6, 0.1, 0.12))
        hers = seen(self.user, crouching, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(hers))
        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_faces_and_people_left_two_by_two_are_somebody_but_nobody_named(self):
        self.anchor(self.lea, RED)
        pair = photo_at(self.user, "pair.jpg", 1)
        named_face(self.user, pair, self.noah, box=(0.15, 0.6, 0.1, 0.12))
        named_face(self.user, pair, None, box=(0.7, 0.6, 0.1, 0.12))
        first = seen(self.user, pair, RED)
        second = seen(self.user, pair, BLUE, box=(0.6, 0.1, 0.3, 0.8))
        back = seen(self.user, photo_at(self.user, "back.jpg", 2), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(first))
        self.assertIsNone(self.suggestion(second))
        # Whoever wears red there is somebody: Léa is no longer the only one.
        self.assertIsNone(self.suggestion(back))

    def test_more_people_left_than_faces_are_still_asked_about(self):
        self.anchor(self.lea, RED)
        group = photo_at(self.user, "group.jpg", 1)
        named_face(self.user, group, self.noah, box=(0.15, 0.6, 0.1, 0.12))
        near = seen(self.user, group, RED)
        seen(self.user, group, BLUE, box=(0.6, 0.1, 0.3, 0.8))

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(near), self.lea.pk)

    def test_a_person_the_user_said_it_is_not_is_never_suggested_again(self):
        self.anchor(self.lea, RED)
        back = seen(
            self.user, photo_at(self.user, "back.jpg", 1), RED, rejected_person=self.lea
        )

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_a_person_the_user_hid_is_never_suggested_anyone(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED, hidden=True)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_someone_the_user_confirmed_names_the_others_in_their_clothes(self):
        seen(self.user, photo_at(self.user, "confirmed.jpg"), RED, person=self.lea)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_a_suggestion_that_no_longer_holds_is_withdrawn(self):
        anchor = self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)
        suggest_owner(self.user.pk)
        # Lea's face was someone else's: nobody names that top any more.
        anchor.file.faces.all().delete()

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_someone_whose_face_got_a_group_loses_their_suggestion(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)
        suggest_owner(self.user.pk)
        named_face(self.user, back.file, None)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_an_answer_given_while_a_pass_ran_is_kept(self):
        self.anchor(self.lea, RED)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)
        (read,) = [
            item
            for item in appearance_suggestions._read(self.user, "fake", UTC, None)
            if item.pk == back.pk
        ]
        # The user says it is not Lea while the pass computes Lea.
        Appearance.objects.filter(pk=back.pk).update(rejected_person=self.lea)

        self.assertEqual(appearance_suggestions._write(read, (self.lea.pk, 0.0)), 0)

        self.assertIsNone(self.suggestion(back))

    def test_around_a_photo_only_the_people_near_it_are_asked_about(self):
        lea = self.anchor(self.lea, RED)
        near = seen(self.user, photo_at(self.user, "near.jpg", 1), RED)
        self.anchor(self.noah, BLUE, hours=48)
        far = seen(self.user, photo_at(self.user, "far.jpg", 49), BLUE)

        suggest_around(lea.file)

        self.assertEqual(self.suggestion(near), self.lea.pk)
        self.assertIsNone(self.suggestion(far))

    def test_around_several_photos_the_people_near_each_are_asked_about(self):
        lea = self.anchor(self.lea, RED)
        near_lea = seen(self.user, photo_at(self.user, "near-lea.jpg", 1), RED)
        noah = self.anchor(self.noah, BLUE, hours=48)
        near_noah = seen(self.user, photo_at(self.user, "near-noah.jpg", 49), BLUE)
        self.anchor(self.lea, RED, hours=96)
        far = seen(self.user, photo_at(self.user, "far.jpg", 97), RED)

        suggest_around_files(self.user.pk, [lea.file_id, noah.file_id])

        self.assertEqual(self.suggestion(near_lea), self.lea.pk)
        self.assertEqual(self.suggestion(near_noah), self.noah.pk)
        self.assertIsNone(self.suggestion(far))

    def _lea_guessed(self, assignment):
        """Lea's group, sure of one face of hers, and another face in it,
        unlike that one, above someone wearing red. Returns someone else in
        red, seen from behind an hour later."""
        sure = named_face(self.user, photo_at(self.user, "sure.jpg", -48), self.lea)
        face = self.anchor(self.lea, RED).file.faces.get()
        Face.objects.filter(pk=sure.pk).update(
            embedding=face_vector(1), assignment=Face.Assignment.CONFIRMED
        )
        Face.objects.filter(pk=face.pk).update(
            embedding=face_vector(0, 1), assignment=assignment
        )
        refresh_clusters([sure.cluster_id])
        return seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

    def test_a_face_the_grouping_is_unsure_of_names_nobody_by_their_clothes(self):
        back = self._lea_guessed(Face.Assignment.AUTO)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

    def test_a_face_the_user_confirmed_names_them_however_unlike_the_rest(self):
        back = self._lea_guessed(Face.Assignment.CONFIRMED)

        suggest_owner(self.user.pk)

        self.assertEqual(self.suggestion(back), self.lea.pk)

    def test_another_users_people_are_left_alone(self):
        self.anchor(self.lea, RED)
        bob = User.objects.create_user(username="bob", password="p")
        opt_in(bob)
        theirs = seen(bob, photo_at(bob, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(theirs))
