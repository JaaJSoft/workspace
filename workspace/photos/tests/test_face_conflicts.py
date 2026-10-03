"""A correction that would put one person twice in a photo: the conflict is
named, and the user settles it instead of being refused."""

from workspace.people.services.persons import create_person
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_grouping import cluster_owner

from .images import CAROL
from .test_face_api import CLUSTERS, FaceApiTestCase, library_photo

FACES = "/api/v1/photos/faces"


class ConflictTestCase(FaceApiTestCase):
    """pair.png holds one face of the Alice cluster and one of the Bob one."""

    def setUp(self):
        super().setUp()
        self.ann = create_person(owner=self.user, display_name="Ann")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=self.ann)
        self.alice_in_pair = self.face("pair.png", self.alice)
        self.bob_in_pair = self.face("pair.png", self.bob)

    def patch(self, url, data):
        return self.client.patch(url, data, content_type="application/json")

    def assert_set_aside(self, face, cluster):
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.REJECTED)
        self.assertEqual(face.rejected_cluster_id, cluster.pk)


class NamingConflictTests(ConflictTestCase):
    def name_bob_ann(self, **extra):
        return self.patch(
            f"{CLUSTERS}/{self.bob.pk}", {"person": str(self.ann.pk), **extra}
        )

    def test_the_conflicting_faces_are_named(self):
        response = self.name_bob_ann()

        self.assertEqual(response.status_code, 409)
        [conflict] = response.json()["conflicts"]
        self.assertEqual(conflict["file"], str(self.photos["pair.png"].pk))
        self.assertEqual(conflict["file_name"], "pair.png")
        self.assertEqual(conflict["incoming"]["uuid"], str(self.bob_in_pair.pk))
        self.assertEqual(conflict["existing"]["uuid"], str(self.alice_in_pair.pk))
        self.assertTrue(conflict["existing"]["crop_url"].endswith("/crop"))
        self.bob.refresh_from_db()
        self.assertIsNone(self.bob.person_id)

    def test_resolving_keeps_the_faces_already_that_person(self):
        response = self.name_bob_ann(resolve=True)

        self.assertEqual(response.status_code, 200)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.person_id, self.ann.pk)
        self.assert_set_aside(self.bob_in_pair, self.bob)
        self.alice_in_pair.refresh_from_db()
        self.assertEqual(self.alice_in_pair.cluster_id, self.alice.pk)

    def test_a_preferred_face_takes_the_photo_over(self):
        response = self.name_bob_ann(resolve=True, prefer=[str(self.bob_in_pair.pk)])

        self.assertEqual(response.status_code, 200)
        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.bob_in_pair.cluster_id, self.bob.pk)
        self.assert_set_aside(self.alice_in_pair, self.alice)


class FaceConflictTests(ConflictTestCase):
    def test_saying_who_a_face_is_names_the_face_already_them(self):
        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)}
        )

        self.assertEqual(response.status_code, 409)
        [conflict] = response.json()["conflicts"]
        self.assertEqual(conflict["existing"]["uuid"], str(self.alice_in_pair.pk))

    def test_replacing_makes_the_face_that_person_instead(self):
        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}",
            {"to_person": str(self.ann.pk), "replace": True},
        )

        self.assertEqual(response.status_code, 200)
        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.bob_in_pair.cluster.person_id, self.ann.pk)
        self.assert_set_aside(self.alice_in_pair, self.alice)

    def test_replacing_into_a_cluster_swaps_the_faces(self):
        refused = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"cluster": str(self.alice.pk)}
        )
        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}",
            {"cluster": str(self.alice.pk), "replace": True},
        )

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(response.status_code, 200)
        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.bob_in_pair.cluster_id, self.alice.pk)
        self.assert_set_aside(self.alice_in_pair, self.alice)

    def test_a_person_in_the_photo_through_another_cluster_is_refused(self):
        # Ann is the Alice cluster (in pair.png) and a Carol cluster that is
        # not: the Carol one being free must not let Ann into pair.png twice.
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        FaceCluster.objects.filter(faces__file__name="carol.png").update(
            person=self.ann
        )

        response = self.patch(
            f"{FACES}/{self.bob_in_pair.pk}", {"to_person": str(self.ann.pk)}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], cluster__person=self.ann
            ).count(),
            1,
        )


class BatchConflictTests(ConflictTestCase):
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

    def test_replacing_sets_the_faces_already_that_person_aside(self):
        result = self.assign([self.bob_in_pair], replace=True).json()

        self.assertEqual(result["done"], [str(self.bob_in_pair.pk)])
        self.assert_set_aside(self.alice_in_pair, self.alice)

    def test_undo_puts_the_face_set_aside_back(self):
        token = self.assign([self.bob_in_pair], replace=True).json()["undo"]

        self.client.post(
            f"{FACES}/undo", {"token": token}, content_type="application/json"
        )

        self.alice_in_pair.refresh_from_db()
        self.bob_in_pair.refresh_from_db()
        self.assertEqual(self.alice_in_pair.cluster_id, self.alice.pk)
        self.assertEqual(self.bob_in_pair.cluster_id, self.bob.pk)

    def test_two_faces_of_one_photo_never_replace_each_other(self):
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=None)

        result = self.assign([self.alice_in_pair, self.bob_in_pair], replace=True)

        self.assertEqual(len(result.json()["done"]), 1)
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], cluster__person=self.ann
            ).count(),
            1,
        )
