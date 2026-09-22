import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.qr import QRTooLarge
from workspace.people.services.persons import create_person
from workspace.people.services.qr import person_qr_code
from workspace.people.services.vcard import parse_vcards, person_to_qr_vcard

User = get_user_model()


class QRVCardTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.person = create_person(
            owner=self.alice,
            display_name="Alice Martin",
            given_name="Alice",
            family_name="Martin",
            organization="Acme",
            title="CTO",
            birthday=datetime.date(1985, 4, 12),
            emails=[{"value": "alice@acme.test", "type": "work"}],
            phones=[{"value": "+33612345678", "type": "cell"}],
            addresses=[
                {
                    "street": "3 rue de la Paix",
                    "city": "Paris",
                    "region": "",
                    "postal_code": "75002",
                    "country": "France",
                    "type": "work",
                }
            ],
            notes="Met at the conference.",
        )

    def test_card_is_version_3(self):
        self.assertIn("VERSION:3.0", person_to_qr_vcard(self.person))

    def test_every_field_a_phone_files_is_there(self):
        card = parse_vcards(person_to_qr_vcard(self.person))[0]
        self.assertEqual(card.fields["display_name"], "Alice Martin")
        self.assertEqual(card.fields["given_name"], "Alice")
        self.assertEqual(card.fields["family_name"], "Martin")
        self.assertEqual(card.fields["organization"], "Acme")
        self.assertEqual(card.fields["title"], "CTO")
        self.assertEqual(card.fields["birthday"], datetime.date(1985, 4, 12))
        self.assertEqual(
            card.fields["emails"], [{"value": "alice@acme.test", "type": "work"}]
        )
        self.assertEqual(
            card.fields["phones"], [{"value": "+33612345678", "type": "cell"}]
        )
        self.assertEqual(card.fields["addresses"][0]["city"], "Paris")
        self.assertEqual(card.fields["notes"], "Met at the conference.")

    def test_photo_and_uid_are_left_out(self):
        """The card has to fit in about two kilobytes; an avatar alone is
        past that, and the UID buys a scanning phone nothing."""
        text = person_to_qr_vcard(self.person)
        self.assertNotIn("PHOTO", text)
        self.assertNotIn("UID", text)

    def test_url_survives_from_the_extra_properties(self):
        self.person.extra_properties = {"URL": [{"value": "https://acme.test"}]}
        self.assertIn("URL:https://acme.test", person_to_qr_vcard(self.person))

    def test_notes_can_be_dropped(self):
        self.assertNotIn("NOTE", person_to_qr_vcard(self.person, include_notes=False))


class PersonQRCodeTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")

    def test_renders_a_code(self):
        person = create_person(owner=self.alice, display_name="Alice")
        self.assertGreaterEqual(person_qr_code(person).version, 1)

    def test_long_notes_are_dropped_rather_than_refused(self):
        person = create_person(
            owner=self.alice, display_name="Alice", notes="word " * 1000
        )
        code = person_qr_code(person)
        self.assertGreaterEqual(code.version, 1)

    def test_a_card_too_large_without_its_notes_is_refused(self):
        person = create_person(
            owner=self.alice,
            display_name="Alice",
            phones=[
                {"value": f"+336123456{index:02d}", "type": "cell"}
                for index in range(200)
            ],
        )
        with self.assertRaises(QRTooLarge):
            person_qr_code(person)
