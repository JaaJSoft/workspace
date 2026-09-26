from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_corrections import hide_face
from workspace.photos.services.face_grouping import cluster_owner

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, faces_png, upload
from .test_face_api import library_photo

User = get_user_model()


@faces_on
class PeoplePageTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def test_offers_to_turn_faces_on_and_says_what_it_does(self):
        response = self.client.get("/photos/people")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turn on face grouping")
        self.assertContains(response, "biometric data")
        self.assertContains(response, "on this server only")

    def test_lists_the_unnamed_clusters_largest_first(self):
        opt_in(self.user)
        library_photo(self.user, "alice-1.png", (ALICE, (40, 50, 100)))
        library_photo(self.user, "alice-2.png", (ALICE, (200, 80, 90)))
        library_photo(self.user, "bob.png", (BOB, (100, 100, 100)))
        cluster_owner(self.user.pk)
        alice = FaceCluster.objects.get(faces__file__name="alice-1.png")
        bob = FaceCluster.objects.get(faces__file__name="bob.png")

        response = self.client.get("/photos/people")

        cards = [card["uuid"] for card in response.context["unnamed"]]
        self.assertEqual(cards, [str(alice.pk), str(bob.pk)])
        self.assertContains(response, f"/photos?cluster={alice.pk}")
        self.assertNotContains(response, "Turn on face grouping")

    def test_hidden_clusters_have_their_own_list(self):
        opt_in(self.user)
        library_photo(self.user, "bob.png", (BOB, (100, 100, 100)))
        cluster_owner(self.user.pk)
        FaceCluster.objects.update(hidden=True)

        visible = self.client.get("/photos/people")
        hidden = self.client.get("/photos/people", {"hidden": "1"})

        self.assertEqual(visible.context["unnamed"], [])
        self.assertEqual(visible.context["hidden_count"], 1)
        self.assertEqual(len(hidden.context["unnamed"]), 1)

    def test_hidden_faces_are_listed_with_the_hidden_people(self):
        opt_in(self.user)
        photo = library_photo(self.user, "bob.png", (BOB, (100, 100, 100)))
        face = Face.objects.get(file=photo)
        hide_face(face)

        visible = self.client.get("/photos/people")
        hidden = self.client.get("/photos/people", {"hidden": "1"})

        self.assertEqual(visible.context["hidden_count"], 1)
        self.assertIsNone(visible.context["hidden_faces"])
        self.assertEqual(hidden.context["hidden_faces"]["total"], 1)
        (item,) = hidden.context["hidden_faces"]["faces"]
        self.assertEqual(item["uuid"], str(face.pk))
        self.assertContains(hidden, "photos-hidden-faces-data")

    def test_shows_the_progress_while_photos_wait(self):
        opt_in(self.user)
        upload(self.user, "new.png", faces_png((ALICE, (40, 50, 100))))

        response = self.client.get("/photos/people")

        self.assertTrue(response.context["analyzing"])
        self.assertContains(response, "Looking for faces")

    def test_the_sidebar_links_to_people(self):
        response = self.client.get("/photos")

        self.assertContains(response, 'href="/photos/people"')

    @override_settings(PHOTOS_FACES_ENABLED=False)
    def test_no_people_tab_on_an_instance_without_faces(self):
        self.assertEqual(self.client.get("/photos/people").status_code, 404)
        self.assertNotContains(self.client.get("/photos"), 'href="/photos/people"')


@faces_on
class PersonTimelineTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.client.force_login(self.user)
        library_photo(self.user, "alice-1.png", (ALICE, (40, 50, 100)))
        library_photo(self.user, "alice-2.png", (ALICE, (200, 80, 90)))
        library_photo(self.user, "bob.png", (BOB, (100, 100, 100)))
        cluster_owner(self.user.pk)
        self.alice = FaceCluster.objects.get(faces__file__name="alice-1.png")

    def _names(self, response):
        names = []
        for entry in response.context["entries"]:
            if entry["kind"] == "day":
                names += [photo.name for photo in entry["photos"]]
            elif entry["kind"] == "photo":
                names.append(entry["file"].name)
        return sorted(names)

    def test_the_timeline_narrows_to_one_person(self):
        response = self.client.get("/photos", {"cluster": str(self.alice.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._names(response), ["alice-1.png", "alice-2.png"])
        self.assertEqual(response.context["cluster"]["uuid"], str(self.alice.pk))
        self.assertEqual(response.context["scope_tabs"], [])
        self.assertTrue(response.context["is_people_view"])

    def test_someone_elses_cluster_is_missing(self):
        other = User.objects.create_user(username="bob", password="p")
        theirs = FaceCluster.objects.create(owner=other)

        response = self.client.get("/photos", {"cluster": str(theirs.pk)})

        self.assertEqual(response.status_code, 404)

    def test_a_malformed_cluster_is_missing(self):
        self.assertEqual(
            self.client.get("/photos", {"cluster": "nope"}).status_code, 404
        )
