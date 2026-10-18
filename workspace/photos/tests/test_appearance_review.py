import json

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.photos.models import Appearance
from workspace.photos.queries import person_photos
from workspace.photos.services import appearance_review
from workspace.photos.services.appearance_review import (
    ALREADY_IN_PHOTO,
    NO_FACE,
    NO_SUGGESTION,
    outfit_suggestions,
)

from .appearances import RED, appearances_on, contact, named_face, photo_at, seen
from .faces import FacesTestMixin, opt_in

User = get_user_model()

BATCH = "/api/v1/photos/appearances/batch"


@appearances_on
class OutfitReviewTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.lea = contact(self.user, "Léa")
        self.noah = contact(self.user, "Noah")
        lea_photo = photo_at(self.user, "lea.jpg")
        named_face(self.user, lea_photo, self.lea)
        self.back = photo_at(self.user, "back.jpg", 1)
        self.row = seen(
            self.user,
            self.back,
            RED,
            suggested_person=self.lea,
            suggestion_distance=0.01,
        )

    def test_a_card_per_person_the_clothes_suggest_nearest_first(self):
        further = seen(
            self.user,
            photo_at(self.user, "back-2.jpg", 2),
            RED,
            suggested_person=self.lea,
            suggestion_distance=0.04,
        )

        (outfits,) = outfit_suggestions(self.user)

        self.assertEqual(outfits.card.person, self.lea)
        self.assertEqual(outfits.appearance_ids, (self.row.pk, further.pk))
        self.assertEqual(outfits.total, 2)

    def test_answered_people_leave_the_review(self):
        seen(self.user, photo_at(self.user, "said.jpg", 2), RED, person=self.lea)
        seen(
            self.user,
            photo_at(self.user, "hidden.jpg", 3),
            RED,
            suggested_person=self.lea,
            hidden=True,
        )

        (outfits,) = outfit_suggestions(self.user)

        self.assertEqual(outfits.appearance_ids, (self.row.pk,))

    def test_saying_yes_makes_the_photo_the_persons(self):
        result = appearance_review.apply(
            self.user, "confirm", [self.row], person=self.lea
        )

        self.assertEqual(result.done, [self.row.pk])
        self.row.refresh_from_db()
        self.assertEqual(self.row.person_id, self.lea.pk)
        self.assertIsNone(self.row.suggested_person_id)
        self.assertIn(self.back, person_photos(self.user, self.lea))

    def test_saying_no_never_suggests_them_again(self):
        appearance_review.apply(self.user, "reject", [self.row], person=self.lea)

        self.row.refresh_from_db()
        self.assertEqual(self.row.rejected_person_id, self.lea.pk)
        self.assertIsNone(self.row.suggested_person_id)
        self.assertNotIn(self.back, person_photos(self.user, self.lea))

    def test_hiding_leaves_nobody_to_name(self):
        appearance_review.apply(self.user, "hide", [self.row])

        self.row.refresh_from_db()
        self.assertTrue(self.row.hidden)
        self.assertEqual(outfit_suggestions(self.user), [])

    def test_someone_else_with_faces_in_the_library_can_be_picked(self):
        named_face(self.user, photo_at(self.user, "noah.jpg", 2), self.noah)

        appearance_review.apply(self.user, "assign", [self.row], person=self.noah)

        self.row.refresh_from_db()
        self.assertEqual(self.row.person_id, self.noah.pk)

    def test_a_contact_with_no_face_in_the_library_cannot_be_picked(self):
        result = appearance_review.apply(
            self.user, "assign", [self.row], person=self.noah
        )

        self.assertEqual(result.skipped, [(self.row.pk, NO_FACE)])
        self.assertIsNone(result.undo)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.person_id)

    def test_an_answer_about_someone_no_longer_suggested_is_left_unsaid(self):
        # Suggested Noah since the card showing Lea was loaded.
        Appearance.objects.filter(pk=self.row.pk).update(suggested_person=self.noah)
        self.row.refresh_from_db()

        confirmed = appearance_review.apply(
            self.user, "confirm", [self.row], person=self.lea
        )
        rejected = appearance_review.apply(
            self.user, "reject", [self.row], person=self.lea
        )

        self.assertEqual(confirmed.skipped, [(self.row.pk, NO_SUGGESTION)])
        self.assertEqual(rejected.skipped, [(self.row.pk, NO_SUGGESTION)])
        self.row.refresh_from_db()
        self.assertIsNone(self.row.person_id)
        self.assertIsNone(self.row.rejected_person_id)
        self.assertEqual(self.row.suggested_person_id, self.noah.pk)

    def test_a_person_already_in_the_photo_is_left_as_they_were(self):
        named_face(self.user, self.back, self.lea, box=(0.65, 0.1, 0.1, 0.1))

        result = appearance_review.apply(
            self.user, "confirm", [self.row], person=self.lea
        )

        self.assertEqual(result.skipped, [(self.row.pk, ALREADY_IN_PHOTO)])

    def test_undo_puts_the_answers_back(self):
        result = appearance_review.apply(
            self.user, "confirm", [self.row], person=self.lea
        )

        self.assertEqual(appearance_review.undo(self.user, result.undo), 1)

        self.row.refresh_from_db()
        self.assertIsNone(self.row.person_id)
        self.assertEqual(self.row.suggested_person_id, self.lea.pk)
        self.assertAlmostEqual(self.row.suggestion_distance, 0.01)
        with self.assertRaises(appearance_review.UndoExpired):
            appearance_review.undo(self.user, result.undo)

    def test_an_undo_is_the_users_own(self):
        result = appearance_review.apply(
            self.user, "confirm", [self.row], person=self.lea
        )
        bob = User.objects.create_user(username="bob", password="p")

        with self.assertRaises(appearance_review.UndoExpired):
            appearance_review.undo(bob, result.undo)


@appearances_on
class AppearanceApiTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.lea = contact(self.user, "Léa")
        named_face(self.user, photo_at(self.user, "lea.jpg"), self.lea)
        self.row = seen(
            self.user,
            photo_at(self.user, "back.jpg", 1),
            RED,
            suggested_person=self.lea,
            suggestion_distance=0.01,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_a_batch_answers_for_the_users_people(self):
        response = self.client.post(
            BATCH,
            {
                "action": "confirm",
                "appearances": [str(self.row.pk)],
                "person": str(self.lea.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["done"], [str(self.row.pk)])
        self.row.refresh_from_db()
        self.assertEqual(self.row.person_id, self.lea.pk)

    def test_another_users_people_are_missing(self):
        bob = User.objects.create_user(username="bob", password="p")
        opt_in(bob)
        client = APIClient()
        client.force_authenticate(bob)
        their_lea = contact(bob, "Léa")

        response = client.post(
            BATCH,
            {
                "action": "confirm",
                "appearances": [str(self.row.pk)],
                "person": str(their_lea.pk),
            },
            format="json",
        )

        self.assertEqual(
            response.json()["skipped"],
            [{"appearance": str(self.row.pk), "reason": "missing"}],
        )
        self.row.refresh_from_db()
        self.assertIsNone(self.row.person_id)

    def test_assign_needs_a_contact_the_user_can_see(self):
        stranger = contact(User.objects.create_user(username="bob", password="p"), "X")

        response = self.client.post(
            BATCH,
            {
                "action": "assign",
                "appearances": [str(self.row.pk)],
                "person": str(stranger.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_confirm_names_the_person_it_confirms(self):
        response = self.client.post(
            BATCH,
            {"action": "confirm", "appearances": [str(self.row.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_hide_names_nobody(self):
        response = self.client.post(
            BATCH,
            {
                "action": "hide",
                "appearances": [str(self.row.pk)],
                "person": str(self.lea.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_the_undo_endpoint_puts_them_back(self):
        token = self.client.post(
            BATCH,
            {"action": "hide", "appearances": [str(self.row.pk)]},
            format="json",
        ).json()["undo"]

        response = self.client.post(
            "/api/v1/photos/appearances/undo", {"token": token}, format="json"
        )

        self.assertEqual(response.json(), {"restored": 1})
        self.assertEqual(
            self.client.post(
                "/api/v1/photos/appearances/undo", {"token": token}, format="json"
            ).status_code,
            410,
        )

    def test_the_crop_is_served_to_its_owner_only(self):
        self.row.crop = default_storage.save(
            f"faces/{self.user.pk}/{self.row.pk}.webp", ContentFile(b"webp")
        )
        self.row.save(update_fields=["crop"])
        url = f"/api/v1/photos/appearances/{self.row.pk}/crop"

        self.assertEqual(self.client.get(url).status_code, 200)
        bob = User.objects.create_user(username="bob", password="p")
        opt_in(bob)
        client = APIClient()
        client.force_authenticate(bob)
        self.assertEqual(client.get(url).status_code, 404)

    def test_the_review_page_asks_about_the_suggested_person(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("photos_ui:people_review"))

        self.assertEqual(response.status_code, 200)
        review = response.context["review"]
        (card,) = [card for card in review["cards"] if card["kind"] == "outfit"]
        self.assertEqual(card["guess"]["uuid"], str(self.lea.pk))
        self.assertEqual([tile["uuid"] for tile in card["faces"]], [str(self.row.pk)])
        self.assertIn(
            f"/api/v1/photos/appearances/{self.row.pk}/crop",
            card["faces"][0]["crop_url"],
        )
        json.dumps(review)
