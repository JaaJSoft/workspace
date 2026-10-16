"""Naming face clusters after contacts from the People module."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import default_storage
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.people.models import Person
from workspace.people.services.avatar import avatar_path
from workspace.people.services.persons import (
    create_person,
    move_to_scope,
    update_person,
)
from workspace.photos.models import Face, FaceCluster
from workspace.photos.queries import user_face_clusters
from workspace.photos.search import search_people
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_people import person_cards, person_clusters

from .images import ALICE, CAROL
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

        response = _patch(
            self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(shared.pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["person_name"], "Grandma")

    def test_naming_after_a_new_name_creates_a_personal_contact(self):
        response = _patch(
            self.client, f"{CLUSTERS}/{self.bob.pk}", {"new_person": "Bob"}
        )

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

        response = _patch(
            self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(theirs.pk)}
        )

        self.assertEqual(response.status_code, 400)
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.person)

    def test_one_person_is_named_once_in_a_photo(self):
        # pair.png holds an Alice face and a Bob face: once both clusters are
        # the same contact, the Bob one there is folded into hers.
        self.alice.person = self.alice_contact
        self.alice.save(update_fields=["person"])
        bob_in_pair = self.face("pair.png", self.bob)

        response = _patch(
            self.client,
            f"{CLUSTERS}/{self.bob.pk}",
            {"person": str(self.alice_contact.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Face.objects.filter(
                file=self.photos["pair.png"], cluster__person=self.alice_contact
            ).count(),
            1,
        )
        bob_in_pair.refresh_from_db()
        self.assertEqual(bob_in_pair.assignment, Face.Assignment.DUPLICATE)

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

        response = _patch(
            self.client, self.url(face), {"to_person": str(self.alice_contact.pk)}
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertEqual(face.cluster_id, self.alice.pk)
        self.assertEqual(face.assignment, Face.Assignment.CONFIRMED)

    def test_a_new_look_starts_another_cluster_of_the_same_person(self):
        # A face nothing like Alice's (a Bob face, in the fake backend) that
        # the user says is Alice: a new look, in a cluster of its own.
        face = self.face("bob.png")

        response = _patch(
            self.client, self.url(face), {"to_person": str(self.alice_contact.pk)}
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertNotEqual(face.cluster_id, self.alice.pk)
        self.assertEqual(face.cluster.person, self.alice_contact)
        self.assertEqual(person_clusters(self.user, self.alice_contact).count(), 2)

    def test_a_person_already_in_the_photo_folds_the_face(self):
        face = self.face("pair.png", self.bob)

        response = _patch(
            self.client, self.url(face), {"to_person": str(self.alice_contact.pk)}
        )

        self.assertEqual(response.status_code, 200)
        face.refresh_from_db()
        self.assertIsNone(face.cluster_id)
        self.assertEqual(face.assignment, Face.Assignment.DUPLICATE)

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
        FaceCluster.objects.filter(pk__in=[self.alice.pk, carol.pk]).update(
            person=contact
        )

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


class MergeIntoPersonTests(FaceApiTestCase):
    def test_a_target_taking_a_name_never_puts_that_person_twice_in_a_photo(self):
        # Bea is the Bob cluster (it has a face in pair.png) and a Carol one.
        # Merging that Carol cluster into the unnamed Alice cluster names it
        # Bea too, and the Alice cluster also has a face in pair.png.
        bea = create_person(owner=self.user, display_name="Bea")
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        carol = FaceCluster.objects.get(faces__file__name="carol.png")
        FaceCluster.objects.filter(pk__in=[self.bob.pk, carol.pk]).update(person=bea)

        response = self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(carol.pk)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        in_pair = Face.objects.filter(
            file=self.photos["pair.png"], cluster__person=bea
        ).count()
        self.assertEqual(in_pair, 1)


class MergeCoverTests(FaceApiTestCase):
    """Merging an unnamed cluster into a person keeps the person's face."""

    def setUp(self):
        super().setUp()
        self.bea = create_person(owner=self.user, display_name="Bea")
        FaceCluster.objects.filter(pk=self.bob.pk).update(person=self.bea)

    def merge_bob_into_alice(self):
        return self.client.post(
            f"{CLUSTERS}/{self.alice.pk}/merge",
            {"clusters": [str(self.bob.pk)]},
            content_type="application/json",
        )

    def test_the_persons_automatic_cover_is_kept(self):
        cover = self.face("bob.png")
        FaceCluster.objects.filter(pk=self.bob.pk).update(cover=cover)

        response = self.merge_bob_into_alice()

        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, cover.pk)
        self.assertIsNone(self.alice.cover_chosen_at)

    def test_a_cover_the_user_picked_stays_picked(self):
        cover = self.face("bob.png")
        picked_at = timezone.now()
        FaceCluster.objects.filter(pk=self.bob.pk).update(
            cover=cover, cover_chosen_at=picked_at
        )

        self.merge_bob_into_alice()

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, cover.pk)
        self.assertEqual(self.alice.cover_chosen_at, picked_at)

    def test_the_persons_cover_wins_a_photo_both_clusters_are_in(self):
        cover = self.face("pair.png", self.bob)
        FaceCluster.objects.filter(pk=self.bob.pk).update(cover=cover)

        self.merge_bob_into_alice()

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, cover.pk)
        self.assertEqual(
            Face.objects.filter(file=self.photos["pair.png"], cluster=self.alice)
            .get()
            .pk,
            cover.pk,
        )

    def test_naming_a_larger_cluster_after_the_person_keeps_their_face(self):
        # The Alice cluster (3 photos) is larger than Bea's only one (2): the
        # card would show its cover once both are Bea.
        response = self.client.patch(
            f"{CLUSTERS}/{self.alice.pk}",
            {"person": str(self.bea.pk)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        (card,) = person_cards(user_face_clusters(self.user))
        self.assertEqual(card.cover_cluster.pk, self.bob.pk)

    def test_merging_into_a_person_keeps_their_cover(self):
        FaceCluster.objects.filter(pk=self.bob.pk).update(person=None)
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=self.bea)
        self.alice.refresh_from_db()
        cover = self.alice.cover_id

        self.merge_bob_into_alice()

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.cover_id, cover)


class PersonsEndpointTests(FaceApiTestCase):
    def test_lists_named_people_with_every_cluster_counted(self):
        contact = create_person(owner=self.user, display_name="Alice")
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        carol = FaceCluster.objects.get(faces__file__name="carol.png")
        FaceCluster.objects.filter(pk__in=[self.alice.pk, carol.pk]).update(
            person=contact
        )

        (alice,) = self.client.get(PERSONS).json()

        self.assertEqual(alice["name"], "Alice")
        self.assertEqual(alice["photo_count"], 4)
        self.assertEqual(
            sorted(alice["clusters"]), sorted([str(self.alice.pk), str(carol.pk)])
        )

    def test_a_search_offers_contacts_without_a_cluster_too(self):
        create_person(owner=self.user, display_name="Alicia")

        results = self.client.get(PERSONS, {"q": "ali"}).json()

        self.assertEqual([r["name"] for r in results], ["Alicia"])
        self.assertEqual(results[0]["photo_count"], 0)

    def test_a_search_ignores_accents_on_either_side(self):
        create_person(owner=self.user, display_name="Hélène")
        create_person(owner=self.user, display_name="Jose")

        def names(query):
            return [r["name"] for r in self.client.get(PERSONS, {"q": query}).json()]

        self.assertEqual(names("helene"), ["Hélène"])
        self.assertEqual(names("JOSÉ"), ["Jose"])

    def test_an_empty_picker_lists_named_people_then_every_other_contact(self):
        contact = create_person(owner=self.user, display_name="Zoé")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=contact)
        create_person(owner=self.user, display_name="bruno")
        create_person(owner=self.user, display_name="Albert")

        results = self.client.get(PERSONS, {"contacts": "1"}).json()

        self.assertEqual([r["name"] for r in results], ["Zoé", "Albert", "bruno"])
        self.assertEqual([r["photo_count"] for r in results], [3, 0, 0])
        self.assertEqual([r["name"] for r in self.client.get(PERSONS).json()], ["Zoé"])

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

    def test_a_contact_created_from_a_cluster_gets_its_cover_as_photo(self):
        _patch(self.client, f"{CLUSTERS}/{self.bob.pk}", {"new_person": "Bob"})

        bob = Person.objects.get(display_name="Bob")
        self.assertTrue(bob.has_avatar)
        self.assertTrue(default_storage.exists(avatar_path(bob)))

    def test_a_contact_created_from_a_face_gets_that_face_as_photo(self):
        face = self.face("bob.png")

        _patch(self.client, f"/api/v1/photos/faces/{face.pk}", {"new_person": "Rob"})

        self.assertTrue(Person.objects.get(display_name="Rob").has_avatar)

    def test_a_face_without_a_crop_still_creates_the_contact(self):
        Face.objects.filter(cluster=self.bob).update(crop="")

        response = _patch(
            self.client, f"{CLUSTERS}/{self.bob.pk}", {"new_person": "Bob"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Person.objects.get(display_name="Bob").has_avatar)

    @patch(
        "workspace.photos.services.face_people.save_avatar",
        side_effect=OSError("broken crop"),
    )
    def test_a_failing_photo_still_creates_the_contact(self, _save):
        face = self.face("bob.png")

        with self.assertLogs("workspace.photos.services.face_people", "ERROR"):
            from_cluster = _patch(
                self.client, f"{CLUSTERS}/{self.bob.pk}", {"new_person": "Bob"}
            )
        with self.assertLogs("workspace.photos.services.face_people", "ERROR"):
            from_face = _patch(
                self.client, f"/api/v1/photos/faces/{face.pk}", {"new_person": "Rob"}
            )

        self.assertEqual(from_cluster.status_code, 200)
        self.assertEqual(from_face.status_code, 200)
        self.assertFalse(Person.objects.get(display_name="Bob").has_avatar)
        self.assertFalse(Person.objects.get(display_name="Rob").has_avatar)

    @patch(
        "workspace.photos.services.face_people.save_avatar",
        side_effect=OSError("broken crop"),
    )
    def test_the_explicit_action_does_not_swallow_a_failing_photo(self, _save):
        contact = create_person(owner=self.user, display_name="Alice")
        FaceCluster.objects.filter(pk=self.alice.pk).update(person=contact)

        with self.assertRaises(OSError):
            self.client.post(f"{CLUSTERS}/{self.alice.pk}/avatar")

    def test_naming_after_an_existing_contact_leaves_their_photo_alone(self):
        contact = create_person(owner=self.user, display_name="Alice")

        _patch(self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(contact.pk)})

        contact.refresh_from_db()
        self.assertFalse(contact.has_avatar)

    def test_an_unnamed_cluster_has_no_contact_to_give_it_to(self):
        response = self.client.post(f"{CLUSTERS}/{self.bob.pk}/avatar")

        self.assertEqual(response.status_code, 400)


class ClusterListQueryTests(FaceApiTestCase):
    def _queries(self):
        # Warmed up first: the first request of a session records presence.
        self.client.get(CLUSTERS)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(CLUSTERS).status_code, 200)
        return len(queries)

    def test_naming_clusters_adds_no_query_per_cluster(self):
        FaceCluster.objects.filter(pk=self.alice.pk).update(
            person=create_person(owner=self.user, display_name="Ann")
        )
        one_named = self._queries()
        FaceCluster.objects.filter(pk=self.bob.pk).update(
            person=create_person(owner=self.user, display_name="Bea")
        )

        self.assertEqual(self._queries(), one_named)


class UnreachableContactTests(FaceApiTestCase):
    """A cluster named after a contact its owner can no longer see - moved to
    another address book, or kept in a group the owner left - reads as
    unnamed, and its avatar action writes nothing to that contact."""

    PRIVATE_NAME = "Victim private name"

    def setUp(self):
        super().setUp()
        self.team = Group.objects.create(name="Team")
        self.victim = User.objects.create_user(username="victim", password="p")
        self.user.groups.add(self.team)
        self.victim.groups.add(self.team)
        self.contact = create_person(group=self.team, display_name="Shared contact")
        response = _patch(
            self.client, f"{CLUSTERS}/{self.alice.pk}", {"person": str(self.contact.pk)}
        )
        self.assertEqual(response.status_code, 200)

    def _move_to_victim(self):
        move_to_scope(self.contact, owner=self.victim)
        update_person(self.contact, display_name=self.PRIVATE_NAME)

    def _cluster_json(self):
        (cluster,) = [
            c
            for c in self.client.get(CLUSTERS).json()
            if c["uuid"] == str(self.alice.pk)
        ]
        return cluster

    def test_the_cluster_list_reads_a_moved_contact_as_unnamed(self):
        self._move_to_victim()

        cluster = self._cluster_json()
        detail = self.client.get(f"{CLUSTERS}/{self.alice.pk}").json()

        self.assertIsNone(cluster["person"])
        self.assertIsNone(cluster["person_name"])
        self.assertIsNone(detail["person_name"])

    def test_the_cluster_list_reads_a_left_groups_contact_as_unnamed(self):
        self.user.groups.remove(self.team)

        cluster = self._cluster_json()

        self.assertIsNone(cluster["person"])
        self.assertIsNone(cluster["person_name"])

    def test_the_persons_endpoint_leaves_a_moved_contact_out(self):
        self._move_to_victim()

        for params in ({}, {"contacts": "1"}):
            body = self.client.get(PERSONS, params).content.decode()
            self.assertNotIn(self.PRIVATE_NAME, body)
            self.assertNotIn(str(self.contact.pk), body)

    def test_the_photo_pages_read_a_moved_contact_as_unnamed(self):
        self._move_to_victim()

        for url, params in (
            ("/photos/people", {}),
            ("/photos/people/review", {}),
            ("/photos", {"cluster": str(self.alice.pk)}),
            ("/photos/people/faces", {"cluster": str(self.alice.pk)}),
        ):
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, self.PRIVATE_NAME)
            self.assertNotContains(response, str(self.contact.pk))

    def test_the_people_page_counts_the_cluster_as_unnamed(self):
        self._move_to_victim()

        response = self.client.get("/photos/people")

        self.assertEqual(response.context["named"], [])
        self.assertIn(
            str(self.alice.pk), [card["uuid"] for card in response.context["unnamed"]]
        )

    def test_search_does_not_find_a_moved_contact(self):
        self._move_to_victim()

        self.assertEqual(search_people("victim", self.user, 10), [])
        self.assertEqual(search_people("shared", self.user, 10), [])

    def test_the_avatar_action_refuses_a_moved_contact(self):
        self._move_to_victim()

        response = self.client.post(f"{CLUSTERS}/{self.alice.pk}/avatar")

        self.assertEqual(response.status_code, 400)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.has_avatar)
        self.assertFalse(default_storage.exists(avatar_path(self.contact)))

    def test_the_avatar_action_refuses_a_left_groups_contact(self):
        self.user.groups.remove(self.team)

        response = self.client.post(f"{CLUSTERS}/{self.alice.pk}/avatar")

        self.assertEqual(response.status_code, 400)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.has_avatar)

    def test_rejoining_the_group_brings_the_name_back(self):
        self.user.groups.remove(self.team)
        self.user.groups.add(self.team)

        self.assertEqual(self._cluster_json()["person_name"], "Shared contact")
