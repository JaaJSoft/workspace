"""What the user confirmed weighs more than what the grouping guessed.

Faces are written with the embedding each test gives them, in the first few
dimensions. The fake backend's grouping threshold is 0.5, so a neighbour is
confident from 0.4 on.
"""

from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.vectors.encoding import from_bytes
from workspace.common.vectors.indexing import index_vector
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services import face_corrections
from workspace.photos.services.face_grouping import (
    LOW_QUALITY,
    assign_faces,
    refresh_clusters,
    release_followers,
)
from workspace.photos.tasks import release_face_followers

from .faces import FacesTestMixin, faces_on, opt_in
from .images import faces_png, upload

User = get_user_model()

AUTO = Face.Assignment.AUTO
CONFIRMED = Face.Assignment.CONFIRMED


def unit(*components):
    vector = np.zeros(FACE_EMBEDDINGS.dims)
    vector[: len(components)] = components
    return vector / np.linalg.norm(vector)


class VectorFacesMixin:
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)

    def face(self, name, vector, *, cluster=None, quality=0.9, assignment=AUTO):
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
            assignment=assignment,
        )
        index_vector(FACE_EMBEDDINGS, face.pk, vector)
        face.refresh_from_db()
        return face

    def cluster_of(self, face):
        return Face.objects.get(pk=face.pk).cluster_id


@faces_on
class ConfirmedWeightTests(VectorFacesMixin, FacesTestMixin, TestCase):
    def test_a_confirmed_neighbour_outweighs_a_closer_automatic_one(self):
        guessed = FaceCluster.objects.create(owner=self.user)
        confirmed = FaceCluster.objects.create(owner=self.user)
        # 0.30 from the newcomer, against 0.35 for the confirmed face.
        self.face("guessed", unit(0.7, np.sqrt(1 - 0.49)), cluster=guessed)
        self.face(
            "confirmed",
            unit(0.65, 0, np.sqrt(1 - 0.65**2)),
            cluster=confirmed,
            assignment=CONFIRMED,
        )
        refresh_clusters([guessed.pk, confirmed.pk])
        newcomer = self.face("new", unit(1))

        assign_faces(self.user.pk, [newcomer.pk])

        self.assertEqual(self.cluster_of(newcomer), confirmed.pk)

    def test_a_confirmed_face_pulls_the_centroid_towards_it(self):
        cluster = FaceCluster.objects.create(owner=self.user)
        self.face("confirmed", unit(1), cluster=cluster, assignment=CONFIRMED)
        self.face("guessed", unit(0, 1), cluster=cluster)

        refresh_clusters([cluster.pk])

        cluster.refresh_from_db()
        centroid = from_bytes(cluster.centroid, FACE_EMBEDDINGS.dims)
        self.assertGreater(centroid[0], 2 * centroid[1])

    def test_a_confirmed_face_vouches_for_a_poor_one_however_poor_it_is(self):
        """The user said who it is: how well the model read it no longer
        matters."""
        cluster = FaceCluster.objects.create(owner=self.user)
        self.face(
            "confirmed",
            unit(1, 0.3),
            cluster=cluster,
            quality=LOW_QUALITY - 0.2,
            assignment=CONFIRMED,
        )
        refresh_clusters([cluster.pk])
        poor = self.face("poor", unit(1), quality=LOW_QUALITY - 0.1)

        assign_faces(self.user.pk, [poor.pk])

        self.assertEqual(self.cluster_of(poor), cluster.pk)


class IntruderMixin(VectorFacesMixin):
    """Three of Ann's faces, confirmed, and Bob's, put with them on its own.

    Bob sits 0.45 from Ann's faces: close enough for the grouping to have
    guessed, far from the cluster as a whole once his face is out of it.
    """

    def setUp(self):
        super().setUp()
        self.cluster = FaceCluster.objects.create(owner=self.user)
        self.ann = [
            self.face(
                f"ann-{k}",
                unit(1, 0, 0, 0.05 * k),
                cluster=self.cluster,
                assignment=CONFIRMED,
            )
            for k in range(3)
        ]
        self.bob = self.face(
            "bob", unit(0.55, np.sqrt(1 - 0.55**2)), cluster=self.cluster
        )
        refresh_clusters([self.cluster.pk])


@faces_on
class ChainTests(IntruderMixin, FacesTestMixin, TestCase):
    def test_a_face_like_the_intruder_but_not_like_the_cluster_stays_out(self):
        # 0.11 from Bob's face, 0.6 from the cluster's centroid.
        newcomer = self.face("bob-2", unit(0.2, 0.9, 0.3))

        assign_faces(self.user.pk, [newcomer.pk])

        self.assertIsNone(self.cluster_of(newcomer))

    def test_a_face_like_the_cluster_still_joins_it(self):
        newcomer = self.face("ann-new", unit(1, 0.1))

        assign_faces(self.user.pk, [newcomer.pk])

        self.assertEqual(self.cluster_of(newcomer), self.cluster.pk)


@faces_on
class ReleaseFollowersTests(IntruderMixin, FacesTestMixin, TestCase):
    """Bob's face brought Bob's other faces in; the user takes it out."""

    def setUp(self):
        super().setUp()
        # Within the threshold of Ann's centroid, nearer Bob's face.
        self.follower = self.face("bob-2", unit(0.6, 0.75), cluster=self.cluster)
        self.ann_guessed = self.face("ann-3", unit(1, 0.05), cluster=self.cluster)
        refresh_clusters([self.cluster.pk])

    def test_the_faces_that_followed_a_face_go_where_the_user_put_it(self):
        bobs = face_corrections.start_cluster(self.bob)

        release_followers(self.user.pk, self.cluster.pk, [self.bob.pk])

        follower = Face.objects.get(pk=self.follower.pk)
        self.assertEqual(follower.cluster_id, bobs.pk)
        self.assertEqual(follower.assignment, AUTO)

    def test_the_faces_that_followed_a_rejected_face_leave_with_it_for_good(self):
        face_corrections.reject_face(self.bob)

        release_followers(self.user.pk, self.cluster.pk, [self.bob.pk])
        assign_faces(self.user.pk, [self.follower.pk])

        follower = Face.objects.get(pk=self.follower.pk)
        self.assertIsNone(follower.cluster_id)
        self.assertEqual(follower.rejected_cluster_id, self.cluster.pk)

    def test_the_cluster_keeps_the_faces_like_itself(self):
        face_corrections.reject_face(self.bob)

        release_followers(self.user.pk, self.cluster.pk, [self.bob.pk])

        self.assertEqual(self.cluster_of(self.ann_guessed), self.cluster.pk)
        self.assertEqual(
            {self.cluster_of(face) for face in self.ann}, {self.cluster.pk}
        )
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.face_count, 4)

    def test_a_follower_the_user_confirmed_stays(self):
        Face.objects.filter(pk=self.follower.pk).update(assignment=CONFIRMED)
        face_corrections.reject_face(self.bob)

        release_followers(self.user.pk, self.cluster.pk, [self.bob.pk])

        self.assertEqual(self.cluster_of(self.follower), self.cluster.pk)

    def test_the_task_suggests_again_around_every_photo_that_moved(self):
        face_corrections.reject_face(self.bob)

        with patch(
            "workspace.photos.services.appearance_suggestions.suggest_around_files"
        ) as suggest:
            release_face_followers(self.user.pk, self.cluster.pk, [self.bob.pk])

        owner_id, file_ids = suggest.call_args.args
        self.assertEqual(owner_id, self.user.pk)
        self.assertEqual(set(file_ids), {self.bob.file_id, self.follower.file_id})
