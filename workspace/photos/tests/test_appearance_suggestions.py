from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.photos.services.appearance_suggestions import (
    suggest_around,
    suggest_owner,
)

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

    def test_two_people_dressed_alike_suggest_neither(self):
        self.anchor(self.lea, RED)
        self.anchor(self.noah, RED, hours=2)
        back = seen(self.user, photo_at(self.user, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(back))

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

    def test_around_a_photo_only_the_people_near_it_are_asked_about(self):
        lea = self.anchor(self.lea, RED)
        near = seen(self.user, photo_at(self.user, "near.jpg", 1), RED)
        self.anchor(self.noah, BLUE, hours=48)
        far = seen(self.user, photo_at(self.user, "far.jpg", 49), BLUE)

        suggest_around(lea.file)

        self.assertEqual(self.suggestion(near), self.lea.pk)
        self.assertIsNone(self.suggestion(far))

    def test_another_users_people_are_left_alone(self):
        self.anchor(self.lea, RED)
        bob = User.objects.create_user(username="bob", password="p")
        opt_in(bob)
        theirs = seen(bob, photo_at(bob, "back.jpg", 1), RED)

        suggest_owner(self.user.pk)

        self.assertIsNone(self.suggestion(theirs))
