"""The people module speaks the reader's language: the page, the contact
panel, the action labels and the API messages its dialogs show."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.people.services.persons import create_person
from workspace.users.services.settings import set_setting

User = get_user_model()


class PeoplePageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.person = create_person(owner=self.user, display_name="Bob Martin")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/people")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mes contacts")
        self.assertContains(response, "Rechercher des contacts...")
        self.assertNotContains(response, "My contacts")

    def test_page_stays_english_by_default(self):
        response = self.client.get("/people")
        self.assertContains(response, "My contacts")

    def test_empty_search_names_the_query_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get(
            "/people", {"q": "zzz"}, HTTP_X_ALPINE_REQUEST="true"
        )
        self.assertContains(response, "Aucun contact ne correspond à « zzz ».")

    def test_panel_translates_entry_types(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get(f"/people/{self.person.uuid}/panel")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Coordonnées")
        self.assertContains(response, '<option value="cell">Mobile</option>')
        self.assertContains(response, '<option value="home">Domicile</option>')

    def test_panel_entry_types_default_to_english(self):
        response = self.client.get(f"/people/{self.person.uuid}/panel")
        self.assertContains(response, '<option value="cell">Cell</option>')


class PeopleApiLanguageTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dominique", password="pass")
        self.person = create_person(owner=self.user, display_name="Bob Martin")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _labels(self):
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": [str(self.person.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        return {a["id"]: a["label"] for a in response.json()[str(self.person.uuid)]}

    def test_action_labels_follow_the_user_language(self):
        set_setting(self.user, "core", "language", "fr")
        labels = self._labels()
        self.assertEqual(labels["qrcode"], "Afficher le QR code")
        self.assertEqual(labels["delete"], "Supprimer")

    def test_action_labels_default_to_english(self):
        self.assertEqual(self._labels()["qrcode"], "Show QR code")

    def test_import_refusal_is_translated(self):
        set_setting(self.user, "core", "language", "fr")
        upload = SimpleUploadedFile(
            "notes.vcf", b"not a card", content_type="text/vcard"
        )
        response = self.client.post(
            "/api/v1/people/import", {"file": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["file"], ["Ce n’est pas un fichier vCard."])

    def test_field_validation_is_translated(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.patch(
            f"/api/v1/people/{self.person.uuid}",
            {"addresses": [{"type": "home"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Une adresse doit comporter au moins une ligne.",
            str(response.json()["addresses"]),
        )
