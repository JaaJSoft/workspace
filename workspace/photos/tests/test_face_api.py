from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.files.services import FileService
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.analysis import analyze_media
from workspace.photos.services.face_analysis import analyze_faces
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
from workspace.users.services.settings import set_setting

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, faces_png, upload

User = get_user_model()

CLUSTERS = "/api/v1/photos/clusters"


def library_photo(user, name, *faces):
    """An uploaded photo, in the library and analyzed for faces."""
    photo = upload(user, name, faces_png(*faces))
    analyze_media(photo)
    analyze_faces(photo)
    return photo


@faces_on
class FaceApiTestCase(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.client.force_login(self.user)
        self.photos = {
            name: library_photo(self.user, name, *faces)
            for name, faces in (
                ("alice-1.png", [(ALICE, (40, 50, 100))]),
                ("alice-2.png", [(ALICE, (200, 80, 90))]),
                ("pair.png", [(ALICE, (20, 50, 100)), (BOB, (250, 50, 100))]),
                ("bob.png", [(BOB, (100, 100, 100))]),
            )
        }
        cluster_owner(self.user.pk)
        self.alice = FaceCluster.objects.get(faces__file__name="alice-1.png")
        self.bob = FaceCluster.objects.get(faces__file__name="bob.png")

    def face(self, name, cluster=None):
        faces = Face.objects.filter(file=self.photos[name])
        if cluster is not None:
            faces = faces.filter(cluster=cluster)
        return faces.get()


class ClusterListTests(FaceApiTestCase):
    def test_lists_the_users_clusters_largest_first(self):
        response = self.client.get(CLUSTERS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(c["uuid"], c["photo_count"]) for c in response.json()],
            [(str(self.alice.pk), 3), (str(self.bob.pk), 2)],
        )
        self.assertTrue(response.json()[0]["cover_url"].endswith("/crop"))

    def test_hidden_clusters_are_listed_apart(self):
        FaceCluster.objects.filter(pk=self.bob.pk).update(hidden=True)

        visible = self.client.get(CLUSTERS).json()
        hidden = self.client.get(CLUSTERS, {"hidden": "true"}).json()

        self.assertEqual([c["uuid"] for c in visible], [str(self.alice.pk)])
        self.assertEqual([c["uuid"] for c in hidden], [str(self.bob.pk)])

    def test_a_trashed_photo_no_longer_counts(self):
        FileService.soft_delete(self.photos["bob.png"], acting_user=self.user)

        (_alice, bob) = self.client.get(CLUSTERS).json()

        self.assertEqual(bob["photo_count"], 1)

    def test_other_users_clusters_are_not_listed(self):
        other = User.objects.create_user(username="bob", password="p")
        opt_in(other)
        self.client.force_login(other)

        self.assertEqual(self.client.get(CLUSTERS).json(), [])
        self.assertEqual(
            self.client.get(f"{CLUSTERS}/{self.alice.pk}").status_code, 404
        )

    def test_nothing_is_listed_once_the_user_turned_faces_off(self):
        set_setting(self.user, MODULE, FACES_ENABLED, False)

        self.assertEqual(self.client.get(CLUSTERS).json(), [])

    @override_settings(PHOTOS_FACES_ENABLED=False)
    def test_every_endpoint_is_missing_on_an_instance_without_faces(self):
        for url in (
            CLUSTERS,
            f"{CLUSTERS}/{self.alice.pk}/photos",
            "/api/v1/photos/faces/status",
            f"/api/v1/photos/faces/{self.face('bob.png').pk}/crop",
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_a_malformed_uuid_is_a_404(self):
        self.assertEqual(self.client.get(f"{CLUSTERS}/not-a-uuid").status_code, 404)


class ClusterPhotosTests(FaceApiTestCase):
    def test_lists_the_photos_of_a_cluster_with_the_files_serializer(self):
        response = self.client.get(f"{CLUSTERS}/{self.alice.pk}/photos")

        self.assertEqual(response.status_code, 200)
        names = sorted(item["name"] for item in response.json())
        self.assertEqual(names, ["alice-1.png", "alice-2.png", "pair.png"])
        self.assertIn("content_url", response.json()[0])

    def test_pages_on_request(self):
        response = self.client.get(f"{CLUSTERS}/{self.alice.pk}/photos", {"limit": 2})

        self.assertEqual(len(response.json()), 2)
        self.assertEqual(response["X-Has-More"], "true")


class ClusterCorrectionTests(FaceApiTestCase):
    def test_hide_a_cluster(self):
        response = self.client.patch(
            f"{CLUSTERS}/{self.bob.pk}",
            {"hidden": True},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.bob.refresh_from_db()
        self.assertTrue(self.bob.hidden)

    def test_set_the_cover(self):
        face = self.face("pair.png", self.alice)

        response = self.client.patch(
            f"{CLUSTERS}/{self.alice.pk}",
            {"cover": str(face.pk)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, face.pk)

    def test_the_cover_must_be_one_of_the_clusters_faces(self):
        response = self.client.patch(
            f"{CLUSTERS}/{self.alice.pk}",
            {"cover": str(self.face("bob.png").pk)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_merge_keeps_one_face_per_photo(self):
        response = self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(self.bob.pk)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FaceCluster.objects.filter(pk=self.bob.pk).exists())
        # pair.png already had its Alice face in the target: its Bob face
        # cannot join, and goes back to ungrouped.
        self.assertEqual(
            sorted(self.alice.faces.values_list("file__name", flat=True)),
            ["alice-1.png", "alice-2.png", "bob.png", "pair.png"],
        )
        self.assertEqual(response.json()["photo_count"], 4)

    def test_merge_refuses_a_cluster_of_someone_else(self):
        other = User.objects.create_user(username="bob", password="p")
        theirs = FaceCluster.objects.create(owner=other)

        response = self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(theirs.pk)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(FaceCluster.objects.filter(pk=theirs.pk).exists())

    def test_ungroup_deletes_the_cluster_and_keeps_its_faces_out(self):
        response = self.client.delete(f"{CLUSTERS}/{self.bob.pk}")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(FaceCluster.objects.filter(pk=self.bob.pk).exists())
        bob_face = self.face("bob.png")
        self.assertEqual(bob_face.assignment, Face.Assignment.REJECTED)

        cluster_owner(self.user.pk)

        bob_face.refresh_from_db()
        self.assertIsNone(bob_face.cluster_id)


class FaceCorrectionTests(FaceApiTestCase):
    def url(self, face):
        return f"/api/v1/photos/faces/{face.pk}"

    def test_not_this_person(self):
        face = self.face("alice-2.png")

        response = self.client.patch(
            self.url(face), {"cluster": None}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.REJECTED)
        self.assertEqual(face.rejected_cluster_id, self.alice.pk)

    def test_this_is_someone_else(self):
        face = self.face("alice-2.png")

        response = self.client.patch(
            self.url(face),
            {"cluster": str(self.bob.pk)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.bob.pk)
        self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)

    def test_confirm_in_place(self):
        face = self.face("alice-2.png")

        response = self.client.patch(
            self.url(face), {"assignment": "confirmed"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)
        self.assertEqual(face.cluster_id, self.alice.pk)

    def test_refuses_a_cluster_already_holding_a_face_of_the_photo(self):
        face = self.face("pair.png", self.bob)

        response = self.client.patch(
            self.url(face),
            {"cluster": str(self.alice.pk)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.bob.pk)

    def test_faces_of_a_photo(self):
        response = self.client.get(
            f"/api/v1/photos/files/{self.photos['pair.png'].pk}/faces"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [f["cluster"] for f in response.json()],
            [str(self.alice.pk), str(self.bob.pk)],
        )
        self.assertEqual(set(response.json()[0]["box"]), {"x", "y", "width", "height"})

    def test_crop_is_a_privately_cached_webp(self):
        response = self.client.get(f"{self.url(self.face('bob.png'))}/crop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("private", response["Cache-Control"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"RIFF"))

    def test_someone_elses_crop_is_missing(self):
        other = User.objects.create_user(username="bob", password="p")
        opt_in(other)
        self.client.force_login(other)

        response = self.client.get(f"{self.url(self.face('bob.png'))}/crop")

        self.assertEqual(response.status_code, 404)


class FaceStatusTests(FaceApiTestCase):
    def test_reports_the_analysis_progress(self):
        upload(self.user, "new.png", faces_png((ALICE, (40, 50, 100))))

        response = self.client.get("/api/v1/photos/faces/status")

        self.assertEqual(response.json(), {"enabled": True, "total": 5, "analyzed": 4})
