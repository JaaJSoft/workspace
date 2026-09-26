"""The review page: naming people one after the other, checking doubtful faces."""

import numpy as np
from django.contrib.auth import get_user_model
from django.test import override_settings

from workspace.common.vectors.encoding import from_bytes, normalize, to_bytes
from workspace.people.services.persons import create_person
from workspace.photos.indexes import FACE_EMBEDDINGS
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_grouping import cluster_owner, refresh_clusters
from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
from workspace.photos.services.face_review import doubtful_faces, unnamed_queue
from workspace.users.services.settings import set_setting

from .images import CAROL
from .test_face_api import CLUSTERS, FaceApiTestCase, library_photo

User = get_user_model()
REVIEW = "/photos/people/review"


def _vector(face):
    return from_bytes(Face.objects.get(pk=face.pk).embedding, FACE_EMBEDDINGS.dims)


def _set_vector(faces, vector):
    Face.objects.filter(pk__in=[f.pk for f in faces]).update(
        embedding=to_bytes(normalize(vector, FACE_EMBEDDINGS.dims))
    )


def _stranger():
    return np.random.default_rng(7).standard_normal(FACE_EMBEDDINGS.dims)


class ReviewTestCase(FaceApiTestCase):
    """Alice (3 photos) is named, Bob (2 photos, one with Alice) is not."""

    def setUp(self):
        super().setUp()
        self.contact = create_person(owner=self.user, display_name="Alice Martin")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=self.contact)

    def _stray_alice_face(self):
        """Give one of Alice's faces a vector unlike the others: it becomes
        the far one of her cluster."""
        stray = self.face("alice-2.png")
        _set_vector([stray], _stranger())
        refresh_clusters([self.alice.pk])
        return stray


class UnnamedQueueTests(ReviewTestCase):
    def test_lists_the_unnamed_clusters_with_their_faces(self):
        (item,) = unnamed_queue(self.user)

        self.assertEqual(item.cluster.pk, self.bob.pk)
        # The cover is shown apart: the samples are the other faces.
        self.bob.refresh_from_db()
        self.assertEqual(
            list(item.sample_face_ids),
            [
                f.pk
                for f in Face.objects.filter(cluster=self.bob)
                if f.pk != self.bob.cover_id
            ],
        )

    def test_hidden_clusters_are_left_out(self):
        FaceCluster.objects.filter(pk=self.bob.pk).update(hidden=True)

        self.assertEqual(unnamed_queue(self.user), [])

    def test_a_cluster_close_to_a_named_person_suggests_them(self):
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        new_look = FaceCluster.objects.get(faces__file__name="carol.png")
        near = _vector(self.face("alice-1.png")) + 0.2 * normalize(
            _stranger(), FACE_EMBEDDINGS.dims
        )
        _set_vector(Face.objects.filter(cluster=new_look), near)
        refresh_clusters([new_look.pk])

        queue = {item.cluster.pk: item for item in unnamed_queue(self.user)}

        self.assertEqual(queue[new_look.pk].suggestion.person, self.contact)
        self.assertIsNone(queue[self.bob.pk].suggestion)

    def test_no_suggestion_of_someone_already_in_one_of_the_photos(self):
        # Bob looks like Alice now, but they are side by side in pair.png:
        # naming Bob after her would be refused.
        _set_vector(
            Face.objects.filter(cluster=self.bob), _vector(self.face("alice-1.png"))
        )
        refresh_clusters([self.bob.pk])

        (item,) = unnamed_queue(self.user)

        self.assertIsNone(item.suggestion)

    def test_a_named_centroid_of_another_size_is_left_out(self):
        # What a backend switch leaves until the rebuild: a vector of the
        # other backend's size.
        FaceCluster.objects.filter(pk=self.alice.pk).update(centroid=b"\0" * 12)

        (item,) = unnamed_queue(self.user)

        self.assertEqual(item.cluster.pk, self.bob.pk)
        self.assertIsNone(item.suggestion)


class DoubtfulFacesTests(ReviewTestCase):
    def test_a_face_far_from_its_named_cluster_is_to_check(self):
        stray = self._stray_alice_face()

        (doubts,) = doubtful_faces(self.user)

        self.assertEqual(doubts.card.person, self.contact)
        self.assertEqual([d.face_id for d in doubts.faces], [stray.pk])
        self.assertEqual(doubts.total, 1)

    def test_faces_close_to_the_centroid_are_not(self):
        self.assertEqual(doubtful_faces(self.user), [])

    def test_a_confirmed_face_is_never_to_check_again(self):
        stray = self._stray_alice_face()
        Face.objects.filter(pk=stray.pk).update(assignment=Face.Assignment.CONFIRMED)

        self.assertEqual(doubtful_faces(self.user), [])

    def test_unnamed_clusters_have_nothing_to_check(self):
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=None)
        self._stray_alice_face()

        self.assertEqual(doubtful_faces(self.user), [])


class ReviewEndpointTests(ReviewTestCase):
    def _review(self, cluster, **body):
        return self.client.post(
            f"{CLUSTERS}/{cluster.pk}/review", body, content_type="application/json"
        )

    def test_confirms_and_rejects_in_one_go(self):
        keep, drop = self.face("alice-1.png"), self.face("alice-2.png")

        response = self._review(
            self.alice, confirmed=[str(keep.pk)], rejected=[str(drop.pk)]
        )

        self.assertEqual(response.status_code, 204)
        keep.refresh_from_db()
        drop.refresh_from_db()
        self.assertEqual(keep.assignment, Face.Assignment.CONFIRMED)
        self.assertEqual(keep.cluster_id, self.alice.pk)
        self.assertIsNone(drop.cluster_id)
        self.assertEqual(drop.assignment, Face.Assignment.REJECTED)
        self.assertEqual(drop.rejected_cluster_id, self.alice.pk)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.face_count, 2)

    def test_a_face_that_left_the_cluster_is_left_alone(self):
        moved = self.face("bob.png")

        self._review(self.alice, confirmed=[str(moved.pk)])

        moved.refresh_from_db()
        self.assertEqual(moved.cluster_id, self.bob.pk)
        self.assertEqual(moved.assignment, Face.Assignment.AUTO)

    def test_a_face_both_confirmed_and_rejected_is_refused(self):
        face = self.face("alice-1.png")

        response = self._review(
            self.alice, confirmed=[str(face.pk)], rejected=[str(face.pk)]
        )

        self.assertEqual(response.status_code, 400)

    def test_another_users_cluster_is_not_found(self):
        other = User.objects.create_user(username="eve", password="p")
        set_setting(other, MODULE, FACES_ENABLED, True)
        self.client.force_login(other)

        response = self._review(self.alice, confirmed=[])

        self.assertEqual(response.status_code, 404)


class ReviewPageTests(ReviewTestCase):
    def test_opens_on_the_people_to_name(self):
        response = self.client.get(REVIEW)

        self.assertEqual(response.status_code, 200)
        review = response.context["review"]
        self.assertEqual(review["queue"], "name")
        self.assertEqual([c["uuid"] for c in review["unnamed"]], [str(self.bob.pk)])

    def test_opens_on_the_faces_to_check_when_everyone_has_a_name(self):
        FaceCluster.objects.filter(pk=self.bob.pk).update(
            person=create_person(owner=self.user, display_name="Bob")
        )
        stray = self._stray_alice_face()

        response = self.client.get(REVIEW)

        review = response.context["review"]
        self.assertEqual(review["queue"], "check")
        (block,) = review["doubts"]
        self.assertEqual(block["person"]["name"], "Alice Martin")
        self.assertEqual([f["uuid"] for f in block["faces"]], [str(stray.pk)])
        self.assertEqual(response.context["doubt_count"], 1)

    def test_the_people_tab_links_to_it(self):
        response = self.client.get("/photos/people")

        self.assertContains(response, f'href="{REVIEW}"')
        self.assertEqual(response.context["unnamed_count"], 1)

    def test_goes_back_to_the_opt_in_when_face_grouping_is_off(self):
        set_setting(self.user, MODULE, FACES_ENABLED, False)

        response = self.client.get(REVIEW)

        self.assertRedirects(response, "/photos/people", fetch_redirect_response=False)

    @override_settings(PHOTOS_FACES_ENABLED=False)
    def test_not_found_without_face_grouping_on_the_instance(self):
        self.assertEqual(self.client.get(REVIEW).status_code, 404)
