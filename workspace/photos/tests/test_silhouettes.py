from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.files.services import FileService
from workspace.photos.models import Appearance
from workspace.photos.services import appearance_review
from workspace.photos.services.appearance_review import (
    ALREADY_IN_PHOTO,
    NO_SUGGESTION,
)
from workspace.photos.services.appearance_suggestions import suggest_owner
from workspace.photos.services.silhouettes import silhouette_groups

from .appearances import (
    BESIDE,
    BLUE,
    EDGE,
    LEGS,
    NOON,
    RED,
    SIDE,
    appearances_on,
    contact,
    named_face,
    seen,
)
from .faces import FacesTestMixin, opt_in
from .images import make_photo

User = get_user_model()

BATCH = "/api/v1/photos/appearances/batch"


class SilhouetteSetupMixin(FacesTestMixin):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.lea = contact(self.user, "Léa")
        self.noah = contact(self.user, "Noah")
        self.holidays = FileService.create_folder(owner=self.user, name="Holidays")
        self._photos = 0

    def photo(self, folder=None, taken=NOON):
        self._photos += 1
        return make_photo(
            self.user,
            f"photo-{self._photos}.jpg",
            None if taken is None else taken + timedelta(minutes=self._photos),
            parent=folder or self.holidays,
        )

    def face_of(self, person, colour=None, folder=None):
        """A photo of the folder showing *person*'s face, and their body
        dressed in *colour* when one is given."""
        photo = self.photo(folder)
        named_face(self.user, photo, person)
        if colour is not None:
            seen(self.user, photo, colour)
        return photo

    def alone(self, colour, folder=None, taken=NOON):
        """Someone seen from behind, alone in a photo of the folder."""
        return seen(self.user, self.photo(folder, taken), colour)


@appearances_on
class SilhouetteGroupTests(SilhouetteSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.face_of(self.lea, RED)
        self.face_of(self.lea)
        self.face_of(self.lea)

    def group_of(self, row):
        (group,) = [
            g for g in silhouette_groups(self.user) if row.pk in g.appearance_ids
        ]
        return group

    def test_people_alone_dressed_like_the_folders_person_are_asked_about_as_them(self):
        first, second = self.alone(RED), self.alone(RED)

        group = self.group_of(first)

        self.assertEqual(set(group.appearance_ids), {first.pk, second.pk})
        self.assertEqual(group.guess.person, self.lea)
        self.assertTrue(group.alone)
        self.assertEqual(group.folder_name, "Holidays")

    def test_dressed_otherwise_they_are_asked_who_they_are(self):
        red, blue = self.alone(RED), self.alone(BLUE)

        self.assertIsNone(self.group_of(blue).guess)
        self.assertNotIn(blue.pk, self.group_of(red).appearance_ids)

    def test_undated_photos_are_asked_about_too(self):
        row = self.alone(RED, taken=None)

        self.assertEqual(self.group_of(row).guess.person, self.lea)

    def test_no_guess_when_nobody_is_in_most_of_the_folders_photos(self):
        for _ in range(3):
            self.face_of(self.noah)
        row = self.alone(RED)

        self.assertIsNone(self.group_of(row).guess)

    def test_no_guess_in_a_folder_with_too_few_photos_of_them(self):
        trip = FileService.create_folder(owner=self.user, name="Trip")
        self.face_of(self.lea, RED, folder=trip)
        self.face_of(self.lea, folder=trip)
        row = self.alone(RED, folder=trip)

        self.assertIsNone(self.group_of(row).guess)

    def test_another_folders_photos_say_nothing(self):
        trip = FileService.create_folder(owner=self.user, name="Trip")
        for _ in range(3):
            self.face_of(self.lea, folder=trip)
        # Léa is the folder's person, but never seen in red there.
        row = self.alone(RED, folder=trip)

        self.assertIsNone(self.group_of(row).guess)

    def test_no_guess_for_people_who_share_their_photo(self):
        photo = self.photo()
        first = seen(self.user, photo, RED)
        second = seen(self.user, photo, RED, box=BESIDE)

        group = self.group_of(first)

        self.assertEqual(set(group.appearance_ids), {first.pk, second.pk})
        self.assertFalse(group.alone)
        self.assertIsNone(group.guess)

    def test_a_passer_by_dressed_like_nobody_else_is_not_asked_about(self):
        photo = self.photo()
        named_face(self.user, photo, self.noah, box=(0.8, 0.1, 0.1, 0.1))
        passer_by = seen(self.user, photo, BLUE)

        groups = silhouette_groups(self.user)

        self.assertFalse(any(passer_by.pk in g.appearance_ids for g in groups))

    def test_people_whose_face_is_off_their_head_are_not_asked_about(self):
        for _ in range(2):
            photo = self.photo()
            # Crouching: the face sits below the top of the box.
            named_face(self.user, photo, self.noah, box=(0.15, 0.6, 0.1, 0.12))
            seen(self.user, photo, BLUE)

        self.assertEqual(silhouette_groups(self.user), [])

    def test_a_second_box_of_someone_named_by_their_face_is_not_asked_about(self):
        for _ in range(2):
            photo = self.photo()
            named_face(self.user, photo, self.noah)
            seen(self.user, photo, BLUE)
            # Sitting: boxed again from the waist down, where no face is.
            seen(self.user, photo, BLUE, box=LEGS)

        self.assertEqual(silhouette_groups(self.user), [])

    def test_a_second_box_off_to_one_side_is_not_asked_about(self):
        for _ in range(2):
            photo = self.photo()
            named_face(self.user, photo, self.noah)
            seen(self.user, photo, BLUE)
            # Boxed again with what they hold out, beside them.
            seen(self.user, photo, BLUE, box=SIDE)

        self.assertEqual(silhouette_groups(self.user), [])

    def test_a_person_boxed_twice_whose_face_is_off_their_head_is_theirs(self):
        for _ in range(2):
            photo = self.photo()
            named_face(self.user, photo, self.noah, box=(0.15, 0.6, 0.1, 0.12))
            seen(self.user, photo, BLUE)
            seen(self.user, photo, BLUE, box=LEGS)

        self.assertEqual(silhouette_groups(self.user), [])

    def test_a_box_over_one_the_user_named_is_not_asked_about(self):
        for _ in range(2):
            photo = self.photo()
            seen(self.user, photo, BLUE)
            seen(self.user, photo, BLUE, box=LEGS, person=self.noah)

        self.assertEqual(silhouette_groups(self.user), [])

    def test_a_person_boxed_twice_is_asked_about_once(self):
        photo = self.photo()
        whole = seen(self.user, photo, BLUE)
        seen(self.user, photo, BLUE, box=LEGS)

        group = self.group_of(whole)

        self.assertEqual(group.appearance_ids, (whole.pk,))
        # Alone in their photo, though boxed twice.
        self.assertTrue(group.alone)

    def test_people_beside_each_other_are_two(self):
        photo = self.photo()
        first = seen(self.user, photo, BLUE)
        second = seen(self.user, photo, BLUE, box=BESIDE)

        self.assertEqual(
            set(self.group_of(first).appearance_ids), {first.pk, second.pk}
        )

    def test_people_said_to_be_someone_their_photo_shows_are_not_asked_again(self):
        rows = []
        for _ in range(2):
            photo = self.photo()
            # Noah's face is on somebody else's head.
            named_face(self.user, photo, self.noah, box=(0.65, 0.1, 0.1, 0.1))
            seen(self.user, photo, RED, box=BESIDE)
            rows.append(seen(self.user, photo, BLUE))
        self.assertEqual(
            set(self.group_of(rows[0]).appearance_ids), {row.pk for row in rows}
        )

        result = appearance_review.apply(self.user, "assign", rows, person=self.noah)

        self.assertEqual(
            sorted(result.skipped), sorted((row.pk, ALREADY_IN_PHOTO) for row in rows)
        )
        asked = {pk for g in silhouette_groups(self.user) for pk in g.appearance_ids}
        self.assertFalse(asked & {row.pk for row in rows})
        appearance_review.undo(self.user, result.undo)
        self.assertEqual(
            set(self.group_of(rows[0]).appearance_ids), {row.pk for row in rows}
        )

    def test_named_after_whoever_their_box_overlaps_they_are_that_person(self):
        rows = []
        for _ in range(2):
            photo = self.photo()
            named_face(self.user, photo, self.noah)
            seen(self.user, photo, RED)
            # Over the edge of Noah's box: too little to tell before the answer.
            rows.append(seen(self.user, photo, BLUE, box=EDGE))
        self.assertEqual(
            set(self.group_of(rows[0]).appearance_ids), {row.pk for row in rows}
        )

        result = appearance_review.apply(self.user, "assign", rows, person=self.noah)

        # Noah boxed a second time: answered, and nobody new to ask about.
        self.assertEqual(result.done, [row.pk for row in rows])
        self.assertEqual(result.skipped, [])
        self.assertEqual(silhouette_groups(self.user), [])
        appearance_review.undo(self.user, result.undo)
        self.assertEqual(
            set(self.group_of(rows[0]).appearance_ids), {row.pk for row in rows}
        )

    def test_two_boxes_of_one_person_named_together_are_both_answered(self):
        named_face(self.user, self.photo(), self.noah)
        photo = self.photo()
        whole = seen(self.user, photo, BLUE)
        edge = seen(self.user, photo, BLUE, box=EDGE)

        result = appearance_review.apply(
            self.user, "assign", [whole, edge], person=self.noah
        )

        self.assertEqual(result.done, [whole.pk, edge.pk])
        whole.refresh_from_db()
        edge.refresh_from_db()
        self.assertEqual(whole.person, self.noah)
        self.assertTrue(edge.hidden)
        self.assertIsNone(edge.person)

    def test_no_guess_for_people_already_said_not_to_be_them(self):
        row = self.alone(RED)
        Appearance.objects.filter(pk=row.pk).update(rejected_person=self.lea)

        self.assertIsNone(self.group_of(row).guess)

    def test_people_named_otherwise_are_not_silhouettes(self):
        by_face = self.face_of(self.noah, BLUE)
        said = self.alone(BLUE)
        Appearance.objects.filter(pk=said.pk).update(person=self.noah)
        suggested = self.alone(BLUE)
        Appearance.objects.filter(pk=suggested.pk).update(suggested_person=self.noah)
        hidden = self.alone(BLUE)
        Appearance.objects.filter(pk=hidden.pk).update(hidden=True)
        rows = Appearance.objects.filter(file=by_face).values_list("pk", flat=True)

        asked = {pk for g in silhouette_groups(self.user) for pk in g.appearance_ids}

        self.assertFalse(asked & {*rows, said.pk, suggested.pk, hidden.pk})

    def test_saying_none_of_them_is_the_folders_person_stops_the_guess(self):
        row = self.alone(RED)

        result = appearance_review.apply(self.user, "reject", [row], person=self.lea)

        self.assertEqual(result.done, [row.pk])
        row.refresh_from_db()
        self.assertEqual(row.rejected_person, self.lea)
        self.assertIsNone(self.group_of(row).guess)

    def test_saying_yes_makes_the_photo_theirs(self):
        row = self.alone(RED)

        appearance_review.apply(self.user, "assign", [row], person=self.lea)

        row.refresh_from_db()
        self.assertEqual(row.person, self.lea)
        self.assertEqual(silhouette_groups(self.user), [])

    def test_the_review_page_has_a_card_per_group(self):
        row = self.alone(RED)
        self.client.force_login(self.user)

        review = self.client.get(reverse("photos_ui:people_review")).context["review"]

        (card,) = [c for c in review["cards"] if c["kind"] == "silhouette"]
        self.assertEqual(card["guess"]["uuid"], str(self.lea.pk))
        self.assertEqual(card["folder"], "Holidays")
        self.assertEqual([tile["uuid"] for tile in card["faces"]], [str(row.pk)])


@appearances_on
class RejectAnswerTests(SilhouetteSetupMixin, TestCase):
    def test_a_person_said_to_be_someone_is_taken_out_of_them(self):
        row = self.alone(RED)
        Appearance.objects.filter(pk=row.pk).update(person=self.lea)
        row.refresh_from_db()

        result = appearance_review.apply(self.user, "reject", [row], person=self.lea)

        self.assertEqual(result.done, [row.pk])
        row.refresh_from_db()
        self.assertIsNone(row.person)
        self.assertEqual(row.rejected_person, self.lea)

    def test_hiding_a_person_said_to_be_someone_stops_them_naming_others(self):
        hidden = self.alone(RED)
        Appearance.objects.filter(pk=hidden.pk).update(person=self.lea)
        hidden.refresh_from_db()
        dressed_alike = self.alone(RED)

        appearance_review.apply(self.user, "hide", [hidden])
        suggest_owner(self.user.pk)

        hidden.refresh_from_db()
        self.assertIsNone(hidden.person)
        dressed_alike.refresh_from_db()
        self.assertIsNone(dressed_alike.suggested_person)

    def test_an_answer_about_someone_they_no_longer_are_is_skipped(self):
        said = self.alone(RED)
        Appearance.objects.filter(pk=said.pk).update(person=self.noah)
        suggested = self.alone(RED)
        Appearance.objects.filter(pk=suggested.pk).update(suggested_person=self.noah)
        rows = list(Appearance.objects.filter(pk__in=[said.pk, suggested.pk]))

        result = appearance_review.apply(self.user, "reject", rows, person=self.lea)

        self.assertEqual(
            sorted(result.skipped),
            sorted([(said.pk, NO_SUGGESTION), (suggested.pk, NO_SUGGESTION)]),
        )
        self.assertEqual(Appearance.objects.get(pk=said.pk).person, self.noah)


@appearances_on
class PersonSilhouettesPageTests(SilhouetteSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.face_of(self.lea)
        self.row = self.alone(RED)
        self.client.force_login(self.user)

    def get(self, **params):
        return self.client.get(
            reverse("photos_ui:person_silhouettes"),
            {"person": str(self.lea.pk), **params},
        )

    def test_the_tab_shows_once_someone_was_said_to_be_them(self):
        faces_url = reverse("photos_ui:person_faces")
        tab = reverse("photos_ui:person_silhouettes")

        before = self.client.get(faces_url, {"person": str(self.lea.pk)})
        Appearance.objects.filter(pk=self.row.pk).update(person=self.lea)
        after = self.client.get(faces_url, {"person": str(self.lea.pk)})

        self.assertNotContains(before, tab)
        self.assertContains(after, f"{tab}?person={self.lea.pk}")

    def test_lists_the_people_said_to_be_them(self):
        Appearance.objects.filter(pk=self.row.pk).update(person=self.lea)
        hidden = self.alone(RED)
        Appearance.objects.filter(pk=hidden.pk).update(person=self.lea, hidden=True)

        response = self.get()

        self.assertEqual(response.status_code, 200)
        board = response.context["board"]
        self.assertEqual([tile["uuid"] for tile in board["faces"]], [str(self.row.pk)])
        self.assertEqual(board["total"], 1)
        self.assertContains(response, "Not Léa")

    def test_taking_them_out_takes_the_photo_out_of_the_persons(self):
        Appearance.objects.filter(pk=self.row.pk).update(person=self.lea)
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.post(
            BATCH,
            {
                "action": "reject",
                "appearances": [str(self.row.pk)],
                "person": str(self.lea.pk),
            },
            format="json",
        )

        self.assertEqual(response.json()["done"], [str(self.row.pk)])
        self.assertEqual(self.get().context["board"]["total"], 0)

    def test_an_unnamed_cluster_has_no_silhouettes(self):
        cluster = named_face(self.user, self.photo(), None).cluster

        response = self.client.get(
            reverse("photos_ui:person_silhouettes"), {"cluster": str(cluster.pk)}
        )

        self.assertEqual(response.status_code, 404)

    def test_someone_elses_contact_is_missing(self):
        eve = User.objects.create_user(username="eve", password="p")
        theirs = contact(eve, "Eve")

        response = self.client.get(
            reverse("photos_ui:person_silhouettes"), {"person": str(theirs.pk)}
        )

        self.assertEqual(response.status_code, 404)
