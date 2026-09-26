from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_analysis import analyze_faces
from workspace.photos.services.face_grouping import (
    LOW_QUALITY,
    cluster_owner,
    dbscan,
    maybe_queue_clustering,
    split_by_photo,
)

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, CAROL, faces_png, upload

User = get_user_model()


def _clusters(user):
    """Each cluster of *user* as the set of photo names its faces come from."""
    return sorted(
        sorted(cluster.faces.values_list("file__name", flat=True))
        for cluster in FaceCluster.objects.filter(owner=user)
    )


@faces_on
class ClusteringTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)

    def _photo(self, name, *faces):
        photo = upload(self.user, name, faces_png(*faces))
        return analyze_faces(photo)

    def test_two_photos_of_one_face_and_one_of_another_make_two_clusters(self):
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        self._photo("alice-2.png", (ALICE, (200, 80, 90)))
        self._photo("bob.png", (BOB, (100, 100, 100)))

        cluster_owner(self.user.pk)

        self.assertEqual(
            _clusters(self.user), [["alice-1.png", "alice-2.png"], ["bob.png"]]
        )

    def test_a_new_photo_joins_the_cluster_its_neighbours_vote_for(self):
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        self._photo("alice-2.png", (ALICE, (200, 80, 90)))
        cluster_owner(self.user.pk)
        (cluster,) = FaceCluster.objects.filter(owner=self.user)

        (face,) = self._photo("alice-3.png", (ALICE, (120, 60, 100)))

        face.refresh_from_db()
        self.assertEqual(face.cluster_id, cluster.pk)
        self.assertEqual(face.assignment, Face.Assignment.AUTO)
        cluster.refresh_from_db()
        self.assertEqual(cluster.face_count, 3)

    def test_two_faces_of_one_photo_never_share_a_cluster(self):
        # Twins: the embeddings say one person, the photo says two.
        self._photo("twins.png", (ALICE, (20, 50, 100)), (ALICE, (250, 50, 100)))
        self._photo("alice.png", (ALICE, (100, 100, 100)))

        cluster_owner(self.user.pk)

        for cluster in FaceCluster.objects.filter(owner=self.user):
            files = list(cluster.faces.values_list("file_id", flat=True))
            self.assertEqual(len(files), len(set(files)))
        self.assertEqual(Face.objects.filter(cluster__isnull=False).count(), 3)

    def test_the_database_refuses_two_faces_of_one_photo_in_a_cluster(self):
        faces = self._photo("twins.png", (ALICE, (20, 50, 100)), (BOB, (250, 50, 100)))
        cluster = FaceCluster.objects.create(owner=self.user)
        Face.objects.filter(pk=faces[0].pk).update(cluster=cluster)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Face.objects.filter(pk=faces[1].pk).update(cluster=cluster)

    def test_a_photo_never_joins_a_cluster_already_holding_one_of_its_faces(self):
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        self._photo("alice-2.png", (ALICE, (200, 80, 90)))
        cluster_owner(self.user.pk)

        faces = self._photo(
            "twins.png", (ALICE, (20, 50, 100)), (ALICE, (250, 50, 100))
        )

        clusters = [Face.objects.get(pk=face.pk).cluster_id for face in faces]
        self.assertEqual(clusters.count(FaceCluster.objects.get().pk), 1)

    def test_a_confirmed_face_survives_a_clustering_run(self):
        (bob,) = self._photo("bob.png", (BOB, (40, 50, 100)))
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        self._photo("alice-2.png", (ALICE, (200, 80, 90)))
        cluster_owner(self.user.pk)
        alice_cluster = FaceCluster.objects.get(faces__file__name="alice-1.png")
        # The user says the Bob face is Alice: the embeddings disagree.
        Face.objects.filter(pk=bob.pk).update(
            cluster=alice_cluster, assignment=Face.Assignment.CONFIRMED
        )

        cluster_owner(self.user.pk)

        bob.refresh_from_db()
        self.assertEqual(bob.cluster_id, alice_cluster.pk)
        self.assertEqual(bob.assignment, Face.Assignment.CONFIRMED)

    def test_a_rejected_face_survives_a_clustering_run(self):
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        (face,) = self._photo("alice-2.png", (ALICE, (200, 80, 90)))
        cluster_owner(self.user.pk)
        cluster = FaceCluster.objects.get()
        Face.objects.filter(pk=face.pk).update(
            cluster=None,
            assignment=Face.Assignment.REJECTED,
            rejected_cluster=cluster,
        )

        cluster_owner(self.user.pk)
        self._photo("alice-3.png", (ALICE, (120, 60, 100)))

        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.REJECTED)
        self.assertEqual(face.rejected_cluster_id, cluster.pk)

    def test_a_low_quality_face_never_seeds_a_cluster(self):
        with patch(
            "workspace.photos.services.detection.fake.FakeFaceBackend.sharpness",
            return_value=0.0,
        ):
            faces = self._photo("blurred-1.png", (CAROL, (40, 50, 30)))
            faces += self._photo("blurred-2.png", (CAROL, (40, 50, 30)))
        self.assertTrue(all(face.quality < LOW_QUALITY for face in faces))

        cluster_owner(self.user.pk)

        self.assertFalse(FaceCluster.objects.exists())

    def test_clusters_stay_within_their_owner(self):
        other = User.objects.create_user(username="bob", password="p")
        opt_in(other)
        self._photo("alice-1.png", (ALICE, (40, 50, 100)))
        analyze_faces(upload(other, "alice-2.png", faces_png((ALICE, (40, 50, 100)))))

        cluster_owner(self.user.pk)
        cluster_owner(other.pk)

        self.assertEqual(_clusters(self.user), [["alice-1.png"]])
        self.assertEqual(_clusters(other), [["alice-2.png"]])

    def test_the_cover_is_the_best_face(self):
        self._photo("small.png", (ALICE, (40, 50, 60)))
        (large,) = self._photo("large.png", (ALICE, (40, 50, 150)))

        cluster_owner(self.user.pk)

        self.assertEqual(FaceCluster.objects.get().cover_id, large.pk)

    @override_settings(PHOTOS_FACES_CLUSTER_PENDING=2)
    def test_enough_ungrouped_faces_queue_a_clustering_run_once(self):
        with (
            patch("workspace.photos.tasks.cluster_faces.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self._photo("alice-1.png", (ALICE, (40, 50, 100)))
            delay.assert_not_called()
            self._photo("bob.png", (BOB, (40, 50, 100)))
            self._photo("carol.png", (CAROL, (40, 50, 100)))
            self.assertFalse(maybe_queue_clustering(self.user.pk))

        delay.assert_called_once_with(self.user.pk)


class DbscanTests(TestCase):
    def _unit(self, *rows):
        vectors = np.array(rows, dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def test_close_good_faces_form_one_cluster_and_the_rest_is_noise(self):
        vectors = self._unit([1, 0, 0], [0.99, 0.1, 0], [0, 1, 0])
        labels = dbscan(vectors, np.array([0.9, 0.9, 0.9]), threshold=0.1)

        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], -1)

    def test_a_poor_face_joins_but_never_bridges_two_clusters(self):
        # a ~ bridge ~ b, but a and b are far apart: only a good bridge links them.
        vectors = self._unit(
            [1, 0.0, 0], [1, 0.45, 0], [1, 0.9, 0], [1, 0.05, 0], [1, 0.85, 0]
        )
        qualities = np.array([0.9, 0.2, 0.9, 0.9, 0.9])
        labels = dbscan(vectors, qualities, threshold=0.06)

        self.assertNotEqual(labels[0], labels[2])

    def test_split_keeps_one_face_per_photo_in_each_group(self):
        vectors = self._unit([1, 0, 0], [1, 0.01, 0], [1, 0.02, 0])
        groups = split_by_photo(
            [0, 1, 2], ["p1", "p1", "p2"], vectors, np.array([0.9, 0.8, 0.7]), 0.1
        )

        for group in groups:
            photos = [["p1", "p1", "p2"][i] for i in group]
            self.assertEqual(len(photos), len(set(photos)))
        self.assertEqual(sorted(i for group in groups for i in group), [0, 1, 2])
