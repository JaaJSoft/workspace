"""Correcting a selection of faces at once, and undoing it."""

from django.contrib.auth import get_user_model

from workspace.people.models import Person
from workspace.people.services.persons import create_person
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_grouping import cluster_owner

from .faces import opt_in
from .images import ALICE, BOB, CAROL
from .test_face_api import FaceApiTestCase, library_photo

User = get_user_model()

BATCH = "/api/v1/photos/faces/batch"
UNDO = "/api/v1/photos/faces/undo"
ACTIONS = "/api/v1/photos/faces/actions"


class FaceBatchTestCase(FaceApiTestCase):
    def batch(self, action, faces, **target):
        return self.client.post(
            BATCH,
            {"action": action, "faces": [str(f.pk) for f in faces], **target},
            content_type="application/json",
        )

    def undo(self, token):
        return self.client.post(UNDO, {"token": token}, content_type="application/json")


class HideTests(FaceBatchTestCase):
    def test_a_hidden_face_leaves_its_cluster_and_grouping(self):
        face = self.face("alice-2.png")

        response = self.batch("hide", [face])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["done"], [str(face.pk)])
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.HIDDEN)
        self.assertEqual(face.rejected_cluster_id, self.alice.pk)
        cluster_owner(self.user.pk)
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)

    def test_unhiding_hands_the_face_back_to_grouping(self):
        face = self.face("alice-2.png")
        self.batch("hide", [face])

        response = self.batch("unhide", [face])

        self.assertEqual(response.json()["done"], [str(face.pk)])
        face.refresh_from_db()
        self.assertEqual(face.assignment, Face.Assignment.AUTO)
        # Never straight back into the cluster the user took it out of.
        self.assertEqual(face.rejected_cluster_id, self.alice.pk)

    def test_a_visible_face_cannot_be_unhidden(self):
        face = self.face("alice-2.png")

        response = self.batch("unhide", [face])

        self.assertEqual(response.json()["done"], [])
        self.assertEqual(
            response.json()["skipped"],
            [{"face": str(face.pk), "reason": "unavailable"}],
        )
        self.assertIsNone(response.json()["undo"])


class AssignTests(FaceBatchTestCase):
    def setUp(self):
        super().setUp()
        self.carol_photo = library_photo(self.user, "carol.png", (CAROL, (40, 40, 90)))
        self.carol = Face.objects.get(file=self.carol_photo)

    def test_faces_join_a_cluster_confirmed(self):
        faces = [self.face("bob.png"), self.carol]

        response = self.batch("assign", faces, cluster=str(self.alice.pk))

        self.assertEqual(len(response.json()["done"]), 2)
        for face in faces:
            face.refresh_from_db()
            self.assertEqual(face.cluster_id, self.alice.pk)
            self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)

    def test_a_face_whose_photo_already_holds_the_person_is_skipped(self):
        bob_in_pair = self.face("pair.png", self.bob)

        response = self.batch(
            "assign", [bob_in_pair, self.carol], cluster=str(self.alice.pk)
        )

        self.assertEqual(response.json()["done"], [str(self.carol.pk)])
        self.assertEqual(
            response.json()["skipped"],
            [{"face": str(bob_in_pair.pk), "reason": "already_in_photo"}],
        )
        bob_in_pair.refresh_from_db()
        self.assertEqual(bob_in_pair.cluster_id, self.bob.pk)

    def test_faces_of_a_new_contact_share_one_new_cluster(self):
        # Two identities the embeddings tell apart: without the shared new
        # cluster, each would start one of its own.
        faces = [self.face("bob.png"), self.carol]

        response = self.batch("assign", faces, new_person="Dana")

        dana = Person.objects.get(display_name="Dana", owner=self.user)
        clusters = FaceCluster.objects.filter(person=dana)
        self.assertEqual(clusters.count(), 1)
        self.assertEqual(clusters.get().face_count, 2)
        self.assertEqual(len(response.json()["done"]), 2)

    def test_faces_join_the_closest_cluster_of_a_contact(self):
        contact = create_person(owner=self.user, display_name="Alice")
        self.alice.person = contact
        self.alice.save(update_fields=["person"])
        face = self.face("alice-2.png")
        self.batch("reject", [face])

        self.batch("assign", [face], person=str(contact.pk))

        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.alice.pk)
        self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)

    def test_someone_new_and_unnamed_gets_one_cluster(self):
        faces = [self.face("bob.png"), self.carol]

        response = self.batch("assign", faces, new_cluster=True)

        cluster = FaceCluster.objects.get(pk=response.json()["cluster"])
        self.assertIsNone(cluster.person)
        self.assertEqual(cluster.face_count, 2)

    def test_assign_takes_exactly_one_target(self):
        response = self.batch(
            "assign", [self.carol], cluster=str(self.alice.pk), new_cluster=True
        )

        self.assertEqual(response.status_code, 400)

    def test_someone_elses_cluster_is_refused(self):
        other = User.objects.create_user(username="eve", password="p")
        opt_in(other)
        theirs = FaceCluster.objects.create(owner=other)

        response = self.batch("assign", [self.carol], cluster=str(theirs.pk))

        self.assertEqual(response.status_code, 400)


class BatchScopeTests(FaceBatchTestCase):
    def test_someone_elses_face_is_skipped_as_missing(self):
        other = User.objects.create_user(username="eve", password="p")
        opt_in(other)
        photo = library_photo(other, "eve.png", (ALICE, (40, 40, 90)))
        theirs = Face.objects.get(file=photo)

        response = self.batch("hide", [theirs])

        self.assertEqual(
            response.json()["skipped"], [{"face": str(theirs.pk), "reason": "missing"}]
        )
        theirs.refresh_from_db()
        self.assertEqual(theirs.assignment, Face.Assignment.AUTO)

    def test_confirming_a_face_in_no_cluster_is_unavailable(self):
        face = self.face("alice-2.png")
        self.batch("reject", [face])

        response = self.batch("confirm", [face])

        self.assertEqual(response.json()["skipped"][0]["reason"], "unavailable")


class UndoTests(FaceBatchTestCase):
    def test_undo_puts_the_faces_back(self):
        faces = [self.face("alice-1.png"), self.face("alice-2.png")]
        token = self.batch("reject", faces).json()["undo"]

        response = self.undo(token)

        self.assertEqual(response.json(), {"restored": 2})
        for face in faces:
            face.refresh_from_db()
            self.assertEqual(face.cluster_id, self.alice.pk)
            self.assertEqual(face.assignment, Face.Assignment.AUTO)
            self.assertIsNone(face.rejected_cluster_id)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.face_count, 3)

    def test_undo_brings_back_a_cluster_the_batch_emptied(self):
        contact = create_person(owner=self.user, display_name="Bob")
        self.bob.person = contact
        self.bob.hidden = True
        self.bob.save(update_fields=["person", "hidden"])
        faces = list(Face.objects.filter(cluster=self.bob))
        token = self.batch("hide", faces).json()["undo"]
        self.assertFalse(FaceCluster.objects.filter(pk=self.bob.pk).exists())

        self.undo(token)

        cluster = FaceCluster.objects.get(pk=self.bob.pk)
        self.assertEqual(cluster.person, contact)
        self.assertTrue(cluster.hidden)
        self.assertEqual(cluster.face_count, 2)

    def test_a_cluster_started_by_the_batch_goes_away_on_undo(self):
        face = self.face("bob.png")
        response = self.batch("assign", [face], new_cluster=True).json()

        self.undo(response["undo"])

        self.assertFalse(FaceCluster.objects.filter(pk=response["cluster"]).exists())
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.bob.pk)

    def test_undo_keeps_the_cover_the_user_picked(self):
        cover = self.face("alice-2.png")
        self.client.patch(
            f"/api/v1/photos/clusters/{self.alice.pk}",
            {"cover": str(cover.pk)},
            content_type="application/json",
        )
        token = self.batch("reject", [cover]).json()["undo"]

        self.undo(token)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, cover.pk)
        self.assertIsNotNone(self.alice.cover_chosen_at)

    def test_a_token_works_once(self):
        token = self.batch("reject", [self.face("alice-2.png")]).json()["undo"]
        self.undo(token)

        self.assertEqual(self.undo(token).status_code, 410)

    def test_someone_elses_token_is_gone(self):
        token = self.batch("reject", [self.face("alice-2.png")]).json()["undo"]
        other = User.objects.create_user(username="eve", password="p")
        opt_in(other)
        self.client.force_login(other)

        self.assertEqual(self.undo(token).status_code, 410)


class FaceActionsTests(FaceBatchTestCase):
    def ids(self, face):
        response = self.client.post(
            ACTIONS, {"uuids": [str(face.pk)]}, content_type="application/json"
        )
        return [a["id"] for a in response.json()[str(face.pk)]]

    def test_a_grouped_face(self):
        self.assertEqual(
            self.ids(self.face("alice-2.png")), ["confirm", "assign", "reject", "hide"]
        )

    def test_a_confirmed_face_is_not_confirmed_again(self):
        face = self.face("alice-2.png")
        self.batch("confirm", [face])

        self.assertEqual(self.ids(face), ["assign", "reject", "hide"])

    def test_a_hidden_face(self):
        face = self.face("alice-2.png")
        self.batch("hide", [face])

        self.assertEqual(self.ids(face), ["assign", "unhide"])

    def test_someone_elses_face_gets_nothing(self):
        other = User.objects.create_user(username="eve", password="p")
        opt_in(other)
        theirs = Face.objects.get(
            file=library_photo(other, "eve.png", (BOB, (40, 40, 90)))
        )

        self.assertEqual(self.ids(theirs), [])
