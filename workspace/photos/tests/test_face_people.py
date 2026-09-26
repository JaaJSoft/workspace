"""Naming face clusters after contacts from the People module."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import default_storage

from workspace.people.models import Person
from workspace.people.services.avatar import avatar_path
from workspace.people.services.persons import create_person
from workspace.photos.models import Face, FaceCluster
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_people import person_clusters

from .images import ALICE, BOB, CAROL
from .test_face_api import CLUSTERS, FaceApiTestCase, library_photo

User = get_user_model()
PERSONS = "/api/v1/photos/persons"


def _patch(client, url, data):
    return client.patch(url, data, content_type="application/json")


class NamingTests(FaceApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice_contact = create_person(owner=self.user, display_name="Alice")

    def test_a_cluster_is_named_after_an_existing_contact(self):
        response = _patch(
            self.client,
            f"{CLUSTERS}/{self.alice.pk}",
            {"person": str(self.alice_contact.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["person_name"], "Alice")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.person, self.alice_contact)

    def test_a_group_contact_can_name_a_cluster(self):
        group = Group.objects.create(name="Family")
        self.user.groups.add(group)
        shared = create_person(group=group, display_name="Grandma")

        response = _patch(self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(shared.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["person_name"], "Grandma")

    def test_naming_after_a_new_name_creates_a_personal_contact(self):
        response = _patch(self.client, f"{CLUSTERS}/{self.bob.pk}", {"new_person": "Bob"})

        self.assertEqual(response.status_code, 200)
        bob = Person.objects.get(display_name="Bob")
        self.assertEqual(bob.owner, self.user)
        self.assertEqual(response.json()["person"], str(bob.pk))

    def test_clearing_the_name_keeps_the_contact(self):
        self.alice.person = self.alice_contact
        self.alice.save(update_fields=["person"])

        response = _patch(self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": None})

        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.person)
        self.assertTrue(Person.objects.filter(pk=self.alice_contact.pk).exists())

    def test_someone_elses_contact_is_refused(self):
        other = User.objects.create_user(username="eve", password="p")
        theirs = create_person(owner=other, display_name="Mallory")

        response = _patch(self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(theirs.pk)})

        self.assertEqual(response.status_code, 400)
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.person)

    def test_one_person_is_never_twice_in_a_photo(self):
        # pair.png holds an Alice face and a Bob face: naming both clusters
        # after the same contact would put that contact twice in it.
        self.alice.person = self.alice_contact
        self.alice.save(update_fields=["person"])

        response = _patch(
            self.client, f"{CLUSTERS}/{self.bob.pk}", {"person": str(self.alice_contact.pk)}
        )

        self.assertEqual(response.status_code, 400)
        self.bob.refresh_from_db()
        self.assertIsNone(self.bob.person)

    def test_deleting_the_contact_leaves_the_cluster_unnamed(self):
        self.alice.person = self.alice_contact
        self.alice.save(update_fields=["person"])
        faces = self.alice.faces.count()

        self.alice_contact.delete()

        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.person)
        self.assertEqual(self.alice.faces.count(), faces)


class FaceToPersonTests(FaceApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice_contact = create_person(owner=self.user, display_name="Alice")
        self.alice.person = self.alice_contact
        self.alice.save(update_fields=["person"])

    def url(self, face):
        return f"/api/v1/photos/faces/{face.pk}"

    def test_a_face_joins_the_persons_closest_cluster(self):
        face = self.face("alice-2.png")
        _patch(self.client, self.url(face), {"cluster": None})

        response = _patch(self.client, self.url(face), {"to_person": str(self.alice_contact.pk)})

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.alice.pk)
        self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)

    def test_a_new_look_starts_another_cluster_of_the_same_person(self):
        # A face nothing like Alice's (a Bob face, in the fake backend) that
        # the user says is Alice: a new look, in a cluster of its own.
        face = self.face("bob.png")

        response = _patch(self.client, self.url(face), {"to_person": str(self.alice_contact.pk)})

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertNotEqual(face.cluster_id, self.alice.pk)
        self.assertEqual(face.cluster.person, self.alice_contact)
        self.assertEqual(person_clusters(self.user, self.alice_contact).count(), 2)

    def test_a_person_already_in_the_photo_is_refused(self):
        face = self.face("pair.png", self.bob)

        response = _patch(self.client, self.url(face), {"to_person": str(self.alice_contact.pk)})

        self.assertEqual(response.status_code, 400)
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.bob.pk)

    def test_a_face_names_someone_new(self):
        face = self.face("bob.png")

        response = _patch(self.client, self.url(face), {"new_person": "Robert"})

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertEqual(face.cluster.person.display_name, "Robert")


class PersonLevelGroupingTests(FaceApiTestCase):
    def test_a_photo_never_gets_one_person_twice_through_two_clusters(self):
        contact = create_person(owner=self.user, display_name="Alice")
        # Alice has two looks: the ALICE faces and the CAROL faces.
        library_photo(self.user, "carol-1.png", (CAROL, (40, 50, 100)))
        library_photo(self.user, "carol-2.png", (CAROL, (200, 80, 90)))
        cluster_owner(self.user.pk)
        carol = FaceCluster.objects.get(faces__file__name="carol-1.png")
        FaceCluster.objects.filter(pk__in=[self.alice.pk, carol.pk]).update(person=contact)

        both = library_photo(
            self.user, "both-looks.png", (ALICE, (20, 50, 100)), (CAROL, (250, 50, 100))
        )

        clusters = set(
            Face.objects.filter(file=both, cluster__isnull=False).values_list(
                "cluster_id", flat=True
            )
        )
        self.assertLessEqual(len(clusters & {self.alice.pk, carol.pk}), 1)


class MergeNamedTests(FaceApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = create_person(owner=self.user, display_name="Ann")
        self.bea = create_person(owner=self.user, display_name="Bea")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=self.ann)
        FaceCluster.objects.filter(pk=self.bob.pk).update(person=self.bea)

    def merge(self, **extra):
        return self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(self.bob.pk)], **extra},
            content_type="application/json",
        )

    def test_merging_two_names_asks_which_one_to_keep(self):
        response = self.merge()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            sorted(p["name"] for p in response.json()["persons"]), ["Ann", "Bea"]
        )
        self.assertTrue(FaceCluster.objects.filter(pk=self.bob.pk).exists())

    def test_the_chosen_name_is_kept(self):
        response = self.merge(person=str(self.bea.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["person_name"], "Bea")

    def test_a_single_name_carries_over(self):
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=None)

        response = self.merge()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["person_name"], "Bea")


class PersonsEndpointTests(FaceApiTestCase):
    def test_lists_named_people_with_every_cluster_counted(self):
        contact = create_person(owner=self.user, display_name="Alice")
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        carol = FaceCluster.objects.get(faces__file__name="carol.png")
        FaceCluster.objects.filter(pk__in=[self.alice.pk, carol.pk]).update(person=contact)

        (alice,) = self.client.get(PERSONS).json()

        self.assertEqual(alice["name"], "Alice")
        self.assertEqual(alice["photo_count"], 4)
        self.assertEqual(sorted(alice["clusters"]), sorted([str(self.alice.pk), str(carol.pk)]))

    def test_a_search_offers_contacts_without_a_cluster_too(self):
        create_person(owner=self.user, display_name="Alicia")

        results = self.client.get(PERSONS, {"q": "ali"}).json()

        self.assertEqual([r["name"] for r in results], ["Alicia"])
        self.assertEqual(results[0]["photo_count"], 0)

    def test_other_users_contacts_are_not_offered(self):
        other = User.objects.create_user(username="eve", password="p")
        create_person(owner=other, display_name="Alicia")

        self.assertEqual(self.client.get(PERSONS, {"q": "ali"}).json(), [])


class AvatarTests(FaceApiTestCase):
    def test_the_cover_becomes_the_contact_photo(self):
        contact = create_person(owner=self.user, display_name="Alice")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=contact)

        response = self.client.post(f"{CLUSTERS}/{self.alice.pk}/avatar")

        self.assertEqual(response.status_code, 204)
        contact.refresh_from_db()
        self.assertTrue(contact.has_avatar)
        self.assertTrue(default_storage.exists(avatar_path(contact)))

    def test_an_unnamed_cluster_has_no_contact_to_give_it_to(self):
        response = self.client.post(f"{CLUSTERS}/{self.bob.pk}/avatar")

        self.assertEqual(response.status_code, 400)
