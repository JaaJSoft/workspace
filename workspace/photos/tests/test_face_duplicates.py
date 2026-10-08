"""A correction that puts a person in a photo or video already showing them:
the newcomer is folded into the face already them, never refused."""

from workspace.people.services.persons import create_person
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_corrections import reject_face
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_review import unassigned_groups

from .images import CAROL
from .test_face_api import CLUSTERS, FaceApiTestCase, library_photo

FACES = "/api/v1/photos/faces"


class PhotoDuplicateTests(FaceApiTestCase):
    """pair.png shows Ann twice (a collage): one face went to the Alice
    cluster, named Ann, the other to the Bob one."""

    def setUp(self):
        super().setUp()
        self.ann = create_person(owner=self.user, display_name="Ann")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=self.ann)
        self.alice_in_pair = self.face("pair.png", self.alice)
        self.bob_in_pair = self.face("pair.png", self.bob)

    def patch(self, url, data):
        return self.client.patch(url, data, content_type="application/json")

    def assign(self, faces, **extra):
        return self.client.post(
            f"{FACES}/batch",
            {
                "action": "assign",
                "faces": [str(f.pk) for f in faces],
                "person": str(self.ann.pk),
                **extra,
            },
            content_type="application/json",
        )

    def assert_folded(self, face):
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.DUPLICATE)

    def assert_still_ann(self):
        self.alice_in_pair.refresh_from_db()
        self.assertEqual(self.alice_in_pair.cluster_id, self.alice.pk)
        self.assertEqual(self.alice_in_pair.assignment, Face.Assignment.AUTO)

    def test_naming_a_cluster_folds_its_face_of_the_photo(self):
        response = self.patch(f"{CLUSTERS}/{self.bob.pk}", {"person": str(self.ann.pk)})

        self.assertEqual(response.status_code, 200)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.person_id, self.ann.pk)
        self.assert_folded(self.bob_in_pair)
        self.assert_still_ann()

    def test_saying_who_a_face_is_folds_it_into_the_face_already_them(self):
        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assert_folded(self.bob_in_pair)
        self.assert_still_ann()

    def test_moving_a_face_into_a_cluster_of_the_photo_folds_it(self):
        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"cluster": str(self.alice.pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assert_folded(self.bob_in_pair)
        self.assert_still_ann()

    def test_a_person_in_the_photo_through_another_cluster_folds_it_too(self):
        # Ann is the Alice cluster (in pair.png) and a Carol cluster that is
        # not: the Carol one being free must not put Ann in pair.png twice.
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        FaceCluster.objects.filter(faces__file__name="carol.png").update(
            person=self.ann
        )

        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assert_folded(self.bob_in_pair)
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], cluster__person=self.ann
            ).count(),
            1,
        )

    def test_a_face_left_out_by_an_earlier_swap_folds_when_named_again(self):
        # What a "use it instead" left behind: the face out of grouping,
        # waiting for a person, refused each time it was named.
        reject_face(self.bob_in_pair)

        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assert_folded(self.bob_in_pair)

    def test_a_folded_face_no_longer_waits_for_a_person(self):
        self.patch(f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)})

        waiting = {
            pk for group in unassigned_groups(self.user) for pk in group.face_ids
        }
        self.assertNotIn(self.bob_in_pair.pk, waiting)

    def test_a_folded_face_can_still_be_named_someone_else(self):
        # Not Ann after all (her twin): the face leaves the fold for its own
        # person.
        self.patch(f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)})

        response = self.patch(f"{FACES}/{self.bob_in_pair.pk}", {"new_person": "Bea"})

        self.assertEqual(response.status_code, 200)
        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.bob_in_pair.cluster.person.display_name, "Bea")
        self.assertEqual(self.bob_in_pair.assignment, Face.Assignment.CONFIRMED)

    def test_a_batch_folds_the_face_instead_of_skipping_it(self):
        result = self.assign([self.bob_in_pair]).json()

        self.assertEqual(result["done"], [str(self.bob_in_pair.pk)])
        self.assertEqual(result["skipped"], [])
        self.assert_folded(self.bob_in_pair)
        self.assert_still_ann()

    def test_two_faces_of_one_photo_given_one_person_make_one(self):
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=None)

        result = self.assign([self.alice_in_pair, self.bob_in_pair]).json()

        self.assertEqual(len(result["done"]), 2)
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], cluster__person=self.ann
            ).count(),
            1,
        )
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], assignment=Face.Assignment.DUPLICATE
            ).count(),
            1,
        )

    def test_undo_brings_the_folded_face_back(self):
        token = self.assign([self.bob_in_pair]).json()["undo"]

        self.client.post(
            f"{FACES}/undo", {"token": token}, content_type="application/json"
        )

        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.bob_in_pair.cluster_id, self.bob.pk)
        self.assertEqual(self.bob_in_pair.assignment, Face.Assignment.AUTO)

    def test_merging_folds_the_second_face_of_the_photo(self):
        response = self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(self.bob.pk)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assert_folded(self.bob_in_pair)
        self.assert_still_ann()


class VideoDuplicateTests(PhotoDuplicateTests):
    """The same, pair.png standing for a video whose tracking split Ann in
    two."""

    def setUp(self):
        super().setUp()
        Face.objects.filter(file=self.photos["pair.png"]).update(timestamp=1.5)
        self.alice_in_pair.refresh_from_db()
        self.bob_in_pair.refresh_from_db()
