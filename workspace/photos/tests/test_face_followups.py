"""What a correction queues once it is saved."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.people.services.persons import create_person
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services import face_corrections, face_people

from .faces import FacesTestMixin, faces_on, opt_in
from .images import faces_png, upload

User = get_user_model()

RELEASE = "workspace.photos.tasks.release_face_followers.delay"
AROUND = "workspace.photos.tasks.suggest_appearances_around.delay"
LIBRARY = "workspace.photos.tasks.suggest_appearances.apply_async"


@faces_on
class FollowupTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.lea = create_person(owner=self.user, display_name="Léa")
        self.noah = create_person(owner=self.user, display_name="Noah")
        self.cluster = FaceCluster.objects.create(owner=self.user, person=self.lea)

    def face(self, name, cluster=None):
        return Face.objects.create(
            file=upload(self.user, f"{name}.png", faces_png()),
            owner=self.user,
            box_x=0.1,
            box_y=0.1,
            box_width=0.2,
            box_height=0.2,
            detector_score=0.99,
            quality=0.9,
            cluster=cluster,
        )

    def test_a_rejected_face_releases_its_followers(self):
        face = self.face("a", self.cluster)

        with (
            patch(RELEASE) as release,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_corrections.reject_face(face)

        release.assert_called_once_with(
            self.user.pk, str(self.cluster.pk), [str(face.pk)]
        )

    def test_rejected_faces_of_a_review_are_released_together(self):
        faces = [self.face(name, self.cluster) for name in ("a", "b")]
        kept = self.face("c", self.cluster)

        with (
            patch(RELEASE) as release,
            patch(AROUND) as around,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_corrections.review_cluster(
                self.cluster,
                confirmed=[kept.pk],
                rejected=[face.pk for face in faces],
            )

        owner, cluster, released = release.call_args.args
        self.assertEqual((owner, cluster), (self.user.pk, str(self.cluster.pk)))
        self.assertEqual(set(released), {str(face.pk) for face in faces})
        around.assert_called_once_with(self.user.pk, [str(kept.pk)])

    def test_a_face_named_as_someone_else_releases_its_followers(self):
        face = self.face("a", self.cluster)

        with (
            patch(RELEASE) as release,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_people.assign_face_to_person(face, self.noah)

        release.assert_called_once_with(
            self.user.pk, str(self.cluster.pk), [str(face.pk)]
        )

    def test_a_face_moved_to_another_group_of_the_same_person_releases_nothing(self):
        face = self.face("a", self.cluster)
        other = FaceCluster.objects.create(owner=self.user, person=self.lea)

        with (
            patch(RELEASE) as release,
            patch(AROUND) as around,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_corrections.confirm_face(face, other)

        release.assert_not_called()
        around.assert_called_once_with(self.user.pk, [str(face.pk)])

    def test_a_face_confirmed_where_it_is_suggests_again_around_it(self):
        face = self.face("a", self.cluster)

        with (
            patch(RELEASE) as release,
            patch(AROUND) as around,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_corrections.confirm_face(face, self.cluster)

        release.assert_not_called()
        around.assert_called_once_with(self.user.pk, [str(face.pk)])

    def test_naming_groups_in_a_row_queues_one_pass_over_the_library(self):
        unnamed = [FaceCluster.objects.create(owner=self.user) for _ in range(2)]

        with (
            patch(LIBRARY) as library,
            self.captureOnCommitCallbacks(execute=True),
        ):
            face_people.link_cluster(unnamed[0], self.noah)
            face_people.link_cluster(unnamed[1], self.lea)

        library.assert_called_once()
        self.assertEqual(library.call_args.args, ((self.user.pk,),))
