from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from workspace.common.vectors.encoding import normalize, to_bytes
from workspace.common.vectors.indexing import index_vector
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services import face_grouping
from workspace.photos.services.face_analysis import analyze_faces
from workspace.photos.services.face_grouping import (
    LOW_QUALITY,
    assign_faces,
    cluster_owner,
    dbscan,
    link_apart,
    maybe_queue_clustering,
    refresh_clusters,
    split_by_photo,
    split_look_alikes,
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

    def test_a_run_offers_only_the_newest_ungrouped_faces_again(self):
        faces = []
        for name in ("a.png", "b.png", "c.png"):
            faces += self._photo(name, (ALICE, (40, 50, 30)))
        Face.objects.update(cluster=None)
        newest = max(faces, key=lambda face: face.created_at)

        with (
            patch("workspace.photos.services.face_grouping._MAX_REVOTED", 1),
            patch(
                "workspace.photos.services.face_grouping.assign_faces",
                wraps=face_grouping.assign_faces,
            ) as assign,
        ):
            cluster_owner(self.user.pk)

        offered = [pk for call in assign.call_args_list for pk in call.args[1]]
        self.assertEqual(offered, [newest.pk])

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


def _unit(*rows):
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class LinkApartTests(TestCase):
    def test_close_faces_group_and_far_ones_stay_apart(self):
        vectors = _unit([1, 0, 0], [1, 0.01, 0], [0, 1, 0])

        groups = link_apart(vectors, ["p1", "p2", "p3"], threshold=0.1)

        self.assertEqual(sorted(sorted(g) for g in groups), [[0, 1], [2]])

    def test_two_faces_of_one_photo_never_share_a_group(self):
        vectors = _unit([1, 0, 0], [1, 0, 0])

        groups = link_apart(vectors, ["p1", "p1"], threshold=0.5)

        self.assertEqual(len(groups), 2)

    def test_look_alikes_seen_together_end_up_in_two_groups(self):
        """Ann and Bob are within the threshold of each other, but photo
        "both" shows them side by side: each one's faces group together,
        and the two groups can never merge."""
        ann, bob = [1, 0.2, 0], [1, -0.2, 0]
        vectors = _unit(
            ann, [1, 0.21, 0], [1, 0.19, 0], bob, [1, -0.21, 0], [1, -0.19, 0]
        )
        files = ["a1", "a2", "both", "b1", "b2", "both"]

        groups = link_apart(vectors, files, threshold=0.5)

        self.assertEqual(sorted(sorted(g) for g in groups), [[0, 1, 2], [3, 4, 5]])

    def test_joined_faces_start_as_one_group(self):
        vectors = _unit([1, 0, 0], [0, 1, 0], [0, 1, 0.01])

        groups = link_apart(vectors, ["p1", "p2", "p3"], threshold=0.1, joined=[0, 1])

        self.assertIn([0, 1], [sorted(g) for g in groups])


@faces_on
@faces_on
class WitnessTests(FacesTestMixin, TestCase):
    """A hard-to-read face sits among a cluster's faces, close to none.

    Each face of the cluster is at a cosine distance of 0.45 from it: within
    the fake backend's threshold (0.5), never confidently (0.4).
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.cluster = FaceCluster.objects.create(owner=self.user)

    def _face(self, name, vector, quality, cluster=None):
        face = Face.objects.create(
            file=upload(self.user, f"{name}.png", faces_png()),
            owner=self.user,
            box_x=0.1,
            box_y=0.1,
            box_width=0.2,
            box_height=0.2,
            detector_score=0.99,
            quality=quality,
            cluster=cluster,
        )
        index_vector(FACE_EMBEDDINGS, face.pk, vector)
        face.refresh_from_db()
        return face

    def _cluster_faces(self, count):
        for k in range(count):
            vector = np.zeros(FACE_EMBEDDINGS.dims)
            vector[0], vector[k + 1] = 0.55, np.sqrt(1 - 0.55**2)
            self._face(f"seen-{k}", vector, 0.9, self.cluster)
        refresh_clusters([self.cluster.pk])
        vector = np.zeros(FACE_EMBEDDINGS.dims)
        vector[0] = 1
        return self._face("hard", vector, LOW_QUALITY - 0.1)

    def test_a_low_quality_face_joins_where_enough_neighbours_are(self):
        face = self._cluster_faces(3)

        assign_faces(self.user.pk, [face.pk])

        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.cluster.pk)

    def test_two_neighbours_are_not_enough(self):
        face = self._cluster_faces(2)

        assign_faces(self.user.pk, [face.pk])

        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)


class SplitLookAlikesTests(FacesTestMixin, TestCase):
    """Ann and Bob look alike; two photos show them together.

    The cluster took all of Ann's faces and Bob's solo ones, and turned Bob
    away from the two photos where Ann already stood for the pair.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.cluster = FaceCluster.objects.create(owner=self.user)
        self.ann, self.bob, self.carol = [], [], []
        photos = {
            name: upload(self.user, f"{name}.png", faces_png())
            for name in ("ann-1", "ann-2", "bob-1", "bob-2", "both-1", "both-2")
        }
        for name in ("ann-1", "ann-2", "both-1", "both-2"):
            self.ann.append(self._face(photos[name], 0.2, self.cluster))
        for name in ("bob-1", "bob-2"):
            self.bob.append(self._face(photos[name], -0.2, self.cluster))
        for name in ("both-1", "both-2"):
            self.bob.append(self._face(photos[name], -0.2, None))
        refresh_clusters([self.cluster.pk])

    def _face(self, photo, lean, cluster, **fields):
        noise = np.random.default_rng(len(self.ann + self.bob)).normal(0, 0.005, 3)
        vector = np.zeros(FACE_EMBEDDINGS.dims)
        vector[:3] = np.array([1, lean, 0]) + noise
        return Face.objects.create(
            file=photo,
            owner=self.user,
            box_x=0.1,
            box_y=0.1,
            box_width=0.2,
            box_height=0.2,
            detector_score=0.99,
            quality=0.9,
            embedding=to_bytes(normalize(vector, FACE_EMBEDDINGS.dims)),
            cluster=cluster,
            **fields,
        )

    def _cluster_of(self, faces):
        return {Face.objects.get(pk=face.pk).cluster_id for face in faces}

    def test_moves_the_turned_away_person_to_a_cluster_of_their_own(self):
        created = split_look_alikes(self.user.pk)

        self.assertEqual(created, 1)
        self.assertEqual(self._cluster_of(self.ann), {self.cluster.pk})
        (bobs,) = self._cluster_of(self.bob)
        self.assertNotIn(bobs, (None, self.cluster.pk))
        self.assertEqual(FaceCluster.objects.get(pk=bobs).face_count, 4)
        self.assertEqual(FaceCluster.objects.get(pk=self.cluster.pk).face_count, 4)

    def test_a_stranger_turned_away_splits_nothing(self):
        """The person next to Ann in a photo is not evidence on their own."""
        Face.objects.filter(pk__in=[f.pk for f in self.bob[:2]]).delete()
        Face.objects.filter(pk=self.bob[3].pk).delete()
        refresh_clusters([self.cluster.pk])

        self.assertEqual(split_look_alikes(self.user.pk), 0)
        self.assertEqual(self._cluster_of(self.ann), {self.cluster.pk})
        self.assertEqual(self._cluster_of(self.bob[2:3]), {None})

    def test_faces_the_user_took_out_are_not_evidence(self):
        Face.objects.filter(cluster__isnull=True).update(
            assignment=Face.Assignment.REJECTED
        )

        self.assertEqual(split_look_alikes(self.user.pk), 0)
        self.assertEqual(self._cluster_of(self.bob[:2]), {self.cluster.pk})

    def test_a_confirmed_face_never_moves(self):
        """Confirming Bob's face says the cluster is Bob: his group keeps it,
        and Ann's faces, with no face of hers turned away, stay too."""
        Face.objects.filter(pk=self.bob[0].pk).update(
            assignment=Face.Assignment.CONFIRMED
        )

        self.assertEqual(split_look_alikes(self.user.pk), 0)
        self.assertEqual(self._cluster_of(self.bob[:2]), {self.cluster.pk})

    def test_a_clustering_run_splits_look_alikes_first(self):
        cluster_owner(self.user.pk)

        self.assertEqual(self._cluster_of(self.ann), {self.cluster.pk})
        self.assertEqual(len(self._cluster_of(self.bob)), 1)
        self.assertNotEqual(self._cluster_of(self.bob), {self.cluster.pk})
