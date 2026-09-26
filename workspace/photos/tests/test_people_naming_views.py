"""Named people in the Photos UI, on a contact's page in People, and in search."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from workspace.people.services.persons import create_person
from workspace.photos.models import FaceCluster
from workspace.photos.search import search_photos
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
from workspace.users.services.settings import set_setting

from .images import CAROL
from .test_face_api import FaceApiTestCase, library_photo

User = get_user_model()


class NamedPeopleTestCase(FaceApiTestCase):
    """Alice is two clusters: the ALICE faces and, a new look, a CAROL one."""

    def setUp(self):
        super().setUp()
        self.contact = create_person(owner=self.user, display_name="Alice Martin")
        library_photo(self.user, "carol.png", (CAROL, (40, 50, 100)))
        cluster_owner(self.user.pk)
        self.carol = FaceCluster.objects.get(faces__file__name="carol.png")
        FaceCluster.objects.filter(pk__in=[self.alice.pk, self.carol.pk]).update(
            person=self.contact
        )


class PeoplePageTests(NamedPeopleTestCase):
    def test_named_people_come_first_one_card_for_all_their_clusters(self):
        response = self.client.get("/photos/people")

        (named,) = response.context["named"]
        self.assertEqual(named["person"]["name"], "Alice Martin")
        self.assertEqual(named["photo_count"], 4)
        self.assertEqual(
            sorted(named["clusters"]), sorted([str(self.alice.pk), str(self.carol.pk)])
        )
        self.assertEqual(
            [card["uuid"] for card in response.context["unnamed"]], [str(self.bob.pk)]
        )
        self.assertContains(response, "Not named yet")
        self.assertContains(response, "Add a name")
        self.assertEqual(response.context["people_count"], 2)


class PersonTimelineTests(NamedPeopleTestCase):
    def _names(self, response):
        names = []
        for entry in response.context["entries"]:
            if entry["kind"] == "day":
                names += [photo.name for photo in entry["photos"]]
            elif entry["kind"] == "photo":
                names.append(entry["file"].name)
        return sorted(names)

    def test_a_persons_page_gathers_every_cluster_of_theirs(self):
        response = self.client.get("/photos", {"person": str(self.contact.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._names(response),
            ["alice-1.png", "alice-2.png", "carol.png", "pair.png"],
        )
        self.assertEqual(response.context["title"], "Alice Martin")
        self.assertIsNone(response.context["cluster"]["uuid"])

    def test_a_contact_in_none_of_the_users_photos_has_no_page(self):
        stranger = create_person(owner=self.user, display_name="Nobody")

        response = self.client.get("/photos", {"person": str(stranger.pk)})

        self.assertEqual(response.status_code, 404)

    def test_someone_elses_contact_has_no_page(self):
        other = User.objects.create_user(username="eve", password="p")
        theirs = create_person(owner=other, display_name="Alice")

        response = self.client.get("/photos", {"person": str(theirs.pk)})

        self.assertEqual(response.status_code, 404)

    def test_a_malformed_person_is_missing(self):
        self.assertEqual(self.client.get("/photos", {"person": "x"}).status_code, 404)


class ContactPageSectionTests(NamedPeopleTestCase):
    def panel(self, person):
        return self.client.get(f"/people/{person.pk}/panel")

    def test_the_contact_page_shows_their_photos(self):
        response = self.panel(self.contact)

        self.assertContains(response, 'data-section="photos"')
        self.assertContains(response, "See all 4 photos in Photos")
        self.assertContains(response, f"/photos?person={self.contact.pk}")

    def test_no_section_for_a_contact_in_no_photo(self):
        stranger = create_person(owner=self.user, display_name="Nobody")

        self.assertNotContains(self.panel(stranger), 'data-section="photos"')

    def test_no_section_once_face_grouping_is_off(self):
        set_setting(self.user, MODULE, FACES_ENABLED, False)

        self.assertNotContains(self.panel(self.contact), 'data-section="photos"')

    def test_a_shared_contact_shows_each_viewer_their_own_photos(self):
        group = Group.objects.create(name="Family")
        self.user.groups.add(group)
        shared = create_person(group=group, display_name="Grandma")
        FaceCluster.objects.filter(pk=self.bob.pk).update(person=shared)
        cousin = User.objects.create_user(username="cousin", password="p")
        cousin.groups.add(group)
        set_setting(cousin, MODULE, FACES_ENABLED, True)
        self.client.force_login(cousin)

        self.assertNotContains(self.panel(shared), 'data-section="photos"')


class SearchTests(NamedPeopleTestCase):
    def test_a_name_finds_the_persons_photos(self):
        results = search_photos("martin", self.user, 10)

        self.assertEqual(results[0].name, "Alice Martin")
        self.assertEqual(results[0].url, f"/photos?person={self.contact.pk}")

    def test_a_contact_in_no_photo_is_left_to_people(self):
        create_person(owner=self.user, display_name="Martin Nobody")

        names = [r.name for r in search_photos("martin", self.user, 10)]

        self.assertEqual(names, ["Alice Martin"])
