"""The vCard mapping, both directions, with no database involved."""

import base64
import datetime
import uuid
from io import BytesIO
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image

from workspace.people.models import Person, PersonList
from workspace.people.services.vcard import (
    VCardError,
    list_to_vcard,
    parse_vcards,
    person_to_vcard,
    serialize_cards,
)

FIXTURES = Path(__file__).parent / "vcards"


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def tiny_jpeg():
    buf = BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class ParseIosTests(SimpleTestCase):
    def setUp(self):
        self.cards = parse_vcards(read_fixture("ios.vcf"))
        self.jane = self.cards[0]

    def test_two_cards_one_person_one_group(self):
        self.assertEqual([c.kind for c in self.cards], ["individual", "group"])

    def test_names(self):
        fields = self.jane.fields
        self.assertEqual(fields["display_name"], "Dr. Jane Marie Doe PhD")
        self.assertEqual(fields["given_name"], "Jane")
        self.assertEqual(fields["family_name"], "Doe")
        # The parts the columns cannot hold survive for the round trip.
        self.assertEqual(
            fields["extra_properties"]["N"], [{"value": "Doe;Jane;Marie;Dr.;PhD"}]
        )

    def test_organization_joins_the_components(self):
        self.assertEqual(self.jane.fields["organization"], "ACME, Engineering")
        self.assertEqual(self.jane.fields["title"], "CTO")

    def test_emails_with_types(self):
        self.assertEqual(
            self.jane.fields["emails"],
            [
                {"value": "jane@acme.example", "type": "work"},
                {"value": "jane@home.example", "type": "home"},
            ],
        )

    def test_phones_normalised_to_e164(self):
        self.assertEqual(
            self.jane.fields["phones"],
            [
                {"value": "+33612345678", "type": "cell"},
                {"value": "+33123456789", "type": "fax"},
            ],
        )

    def test_address(self):
        self.assertEqual(
            self.jane.fields["addresses"],
            [
                {
                    "street": "1 rue de la Paix",
                    "city": "Paris",
                    "region": "",
                    "postal_code": "75001",
                    "country": "France",
                    "type": "home",
                }
            ],
        )

    def test_birthday_and_notes(self):
        self.assertEqual(self.jane.fields["birthday"], datetime.date(1985, 4, 12))
        self.assertEqual(self.jane.fields["notes"], "Met at FOSDEM.\nBrings cake.")

    def test_uid(self):
        self.assertEqual(self.jane.uid, "1B7C0F3A-2E5D-4C8B-9F1E-3A2B1C0D9E8F")

    def test_unknown_properties_land_in_extra_with_group_and_params(self):
        extra = self.jane.fields["extra_properties"]
        self.assertEqual(
            extra["URL"],
            [
                {
                    "value": "https://jane.example",
                    "params": {"TYPE": ["pref"]},
                    "group": "item2",
                }
            ],
        )
        self.assertEqual(
            extra["X-ABLABEL"], [{"value": "_$!<HomePage>!$_", "group": "item2"}]
        )
        self.assertEqual(extra["X-ABADR"], [{"value": "fr", "group": "item1"}])
        self.assertEqual(
            extra["X-SOCIALPROFILE"],
            [
                {
                    "value": "https://twitter.com/janedoe",
                    "params": {"TYPE": ["twitter"]},
                }
            ],
        )

    def test_metadata_and_mapped_properties_are_not_in_extra(self):
        extra = self.jane.fields["extra_properties"]
        for name in ("VERSION", "PRODID", "REV", "FN", "EMAIL", "TEL", "ADR", "UID"):
            self.assertNotIn(name, extra)

    def test_apple_group(self):
        group = self.cards[1]
        self.assertEqual(group.kind, "group")
        self.assertEqual(group.fields["display_name"], "Friends")
        self.assertEqual(group.uid, "7D5E2B10-9A8C-4F3E-B2D1-5C6A7B8C9D0E")
        self.assertEqual(
            group.member_uids,
            [
                "urn:uuid:1B7C0F3A-2E5D-4C8B-9F1E-3A2B1C0D9E8F",
                "urn:uuid:00000000-0000-0000-0000-00000000dead",
            ],
        )


class ParseGoogleTests(SimpleTestCase):
    def setUp(self):
        self.cards = parse_vcards(read_fixture("google.vcf"))

    def test_untyped_email_is_other(self):
        self.assertEqual(
            self.cards[0].fields["emails"],
            [{"value": "john@example.com", "type": "other"}],
        )

    def test_national_number_without_region_is_kept_verbatim(self):
        self.assertEqual(
            self.cards[0].fields["phones"],
            [
                {"value": "+15551234567", "type": "cell"},
                {"value": "555-000-1111", "type": "home"},
            ],
        )

    def test_categories_and_nickname_are_extra(self):
        extra = self.cards[0].fields["extra_properties"]
        self.assertEqual(extra["CATEGORIES"], [{"value": "myContacts,Friends"}])
        self.assertEqual(extra["NICKNAME"], [{"value": "Johnny"}])

    def test_card_without_uid(self):
        self.assertEqual(self.cards[1].uid, "")
        self.assertEqual(self.cards[1].fields["display_name"], "Globex Support")


class ParseNextcloudTests(SimpleTestCase):
    def setUp(self):
        self.cards = parse_vcards(read_fixture("nextcloud.vcf"))

    def test_vcard_4_person(self):
        marie = self.cards[0]
        self.assertEqual(marie.uid, "0a2e4c6f-1b3d-4e5f-8a9b-0c1d2e3f4a5b")
        self.assertEqual(marie.fields["birthday"], datetime.date(1867, 11, 7))
        self.assertEqual(
            marie.fields["phones"], [{"value": "+48123456789", "type": "cell"}]
        )
        extra = marie.fields["extra_properties"]
        self.assertEqual(extra["GENDER"], [{"value": "F"}])
        self.assertEqual(extra["LANG"], [{"value": "pl"}])
        self.assertNotIn("BDAY", extra)

    def test_kind_group(self):
        group = self.cards[1]
        self.assertEqual(group.kind, "group")
        self.assertEqual(group.fields["display_name"], "Physicists")
        self.assertEqual(
            group.member_uids, ["urn:uuid:0a2e4c6f-1b3d-4e5f-8a9b-0c1d2e3f4a5b"]
        )


class ParseEdgeCaseTests(SimpleTestCase):
    def test_garbage_raises(self):
        with self.assertRaises(VCardError):
            parse_vcards("this is not a vcard")

    def test_empty_input_yields_nothing(self):
        self.assertEqual(parse_vcards(""), [])

    def test_display_name_falls_back_to_n_then_org_then_email(self):
        cards = parse_vcards(
            "BEGIN:VCARD\nVERSION:3.0\nN:Doe;Jane;;;\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nORG:ACME\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nEMAIL:a@b.example\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nNOTE:nothing\nEND:VCARD\n"
        )
        self.assertEqual(
            [c.fields["display_name"] for c in cards],
            ["Jane Doe", "ACME", "a@b.example", "Unnamed"],
        )

    def test_names_longer_than_the_columns_are_cut_to_fit(self):
        long_name = "x" * 300
        (card,) = parse_vcards(
            f"BEGIN:VCARD\nVERSION:4.0\nFN:{long_name}\nN:{long_name};{long_name};;;\n"
            f"ORG:{long_name}\nTITLE:{long_name}\nEND:VCARD\n"
        )
        for name in (
            "display_name",
            "given_name",
            "family_name",
            "organization",
            "title",
        ):
            with self.subTest(name):
                self.assertEqual(len(card.fields[name]), 255)

    def test_uid_longer_than_the_column_is_refused(self):
        with self.assertRaises(VCardError):
            parse_vcards(
                f"BEGIN:VCARD\nVERSION:4.0\nFN:X\nUID:{'u' * 256}\nEND:VCARD\n"
            )

    def test_partial_birthday_is_kept_as_extra(self):
        (card,) = parse_vcards(
            "BEGIN:VCARD\nVERSION:4.0\nFN:X\nBDAY:--0412\nEND:VCARD\n"
        )
        self.assertIsNone(card.fields["birthday"])
        self.assertEqual(card.fields["extra_properties"]["BDAY"], [{"value": "--0412"}])

    def test_photo_base64_v3(self):
        photo = tiny_jpeg()
        encoded = base64.b64encode(photo).decode()
        text = (
            "BEGIN:VCARD\nVERSION:3.0\nFN:X\n"
            f"PHOTO;ENCODING=b;TYPE=JPEG:{encoded}\nEND:VCARD\n"
        )
        (card,) = parse_vcards(text)
        self.assertEqual(card.photo, photo)
        self.assertNotIn("PHOTO", card.fields["extra_properties"])

    def test_photo_data_uri_v4(self):
        photo = tiny_jpeg()
        encoded = base64.b64encode(photo).decode()
        text = (
            "BEGIN:VCARD\nVERSION:4.0\nFN:X\n"
            f"PHOTO:data:image/jpeg;base64,{encoded}\nEND:VCARD\n"
        )
        (card,) = parse_vcards(text)
        self.assertEqual(card.photo, photo)

    def test_remote_photo_is_kept_as_extra_not_fetched(self):
        (card,) = parse_vcards(
            "BEGIN:VCARD\nVERSION:4.0\nFN:X\n"
            "PHOTO;VALUE=uri:https://x.example/p.jpg\nEND:VCARD\n"
        )
        self.assertIsNone(card.photo)
        self.assertEqual(
            card.fields["extra_properties"]["PHOTO"],
            [{"value": "https://x.example/p.jpg", "params": {"VALUE": ["uri"]}}],
        )

    def test_a_later_photo_does_not_replace_the_decoded_one(self):
        # The shape of Nextcloud's example contact: the image, then empty URIs.
        photo = tiny_jpeg()
        encoded = base64.b64encode(photo).decode()
        (card,) = parse_vcards(
            "BEGIN:VCARD\nVERSION:3.0\nFN:X\n"
            f"PHOTO;ENCODING=b;TYPE=JPEG:{encoded}\n"
            "PHOTO;VALUE=URI:\nPHOTO;VALUE=URI:\nEND:VCARD\n"
        )
        self.assertEqual(card.photo, photo)
        self.assertEqual(len(card.fields["extra_properties"]["PHOTO"]), 2)

    def test_tel_uri_prefix_is_stripped(self):
        (card,) = parse_vcards(
            "BEGIN:VCARD\nVERSION:4.0\nFN:X\n"
            "TEL;VALUE=uri;TYPE=voice:tel:+33-6-12-34-56-78\nEND:VCARD\n"
        )
        self.assertEqual(
            card.fields["phones"], [{"value": "+33612345678", "type": "other"}]
        )

    def test_multiline_address_lines_fold_into_street(self):
        (card,) = parse_vcards(
            "BEGIN:VCARD\nVERSION:3.0\nFN:X\n"
            "ADR;TYPE=WORK:PO Box 1;Suite 2;3 Road;Town;;;\nEND:VCARD\n"
        )
        self.assertEqual(
            card.fields["addresses"][0]["street"], "PO Box 1\nSuite 2\n3 Road"
        )
        self.assertEqual(card.fields["addresses"][0]["type"], "work")


def make_person(**overrides):
    fields = {
        "uuid": uuid.uuid4(),
        "display_name": "Jane Doe",
        "given_name": "Jane",
        "family_name": "Doe",
        "organization": "ACME",
        "title": "CTO",
        "birthday": datetime.date(1985, 4, 12),
        "emails": [
            {"value": "jane@acme.example", "type": "work"},
            {"value": "jane@home.example", "type": "other"},
        ],
        "phones": [{"value": "+33612345678", "type": "cell"}],
        "addresses": [
            {
                "street": "1 rue de la Paix\nEscalier B",
                "city": "Paris",
                "region": "",
                "postal_code": "75001",
                "country": "France",
                "type": "home",
            }
        ],
        "notes": "Met at FOSDEM; brings cake, always.",
        "extra_properties": {
            "URL": [
                {
                    "value": "https://jane.example",
                    "params": {"TYPE": ["pref"]},
                    "group": "item2",
                }
            ],
            "X-ABLABEL": [{"value": "_$!<HomePage>!$_", "group": "item2"}],
            "NICKNAME": [{"value": "JD"}],
        },
    }
    fields.update(overrides)
    return Person(**fields)


class ExportTests(SimpleTestCase):
    def test_serialize_is_vcard_4_with_our_uuid_as_uid(self):
        person = make_person()
        text = serialize_cards([person_to_vcard(person)])
        self.assertTrue(text.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n"))
        self.assertIn(f"UID:urn:uuid:{person.uuid}\r\n", text)
        self.assertIn("FN:Jane Doe\r\n", text)
        self.assertIn("N:Doe;Jane;;;\r\n", text)
        self.assertIn("EMAIL;TYPE=work:jane@acme.example\r\n", text)
        self.assertIn("EMAIL:jane@home.example\r\n", text)
        self.assertIn("TEL;TYPE=cell:+33612345678\r\n", text)
        self.assertIn("BDAY:19850412\r\n", text)
        self.assertIn("item2.URL;TYPE=pref:https://jane.example\r\n", text)

    def test_import_uid_is_emitted_when_present(self):
        person = make_person(import_uid="abc-123")
        self.assertIn("UID:abc-123\r\n", serialize_cards([person_to_vcard(person)]))

    def test_round_trip_preserves_every_field(self):
        person = make_person()
        (card,) = parse_vcards(serialize_cards([person_to_vcard(person)]))
        for name in (
            "display_name",
            "given_name",
            "family_name",
            "organization",
            "title",
            "birthday",
            "emails",
            "phones",
            "addresses",
            "notes",
            "extra_properties",
        ):
            with self.subTest(name):
                self.assertEqual(card.fields[name], getattr(person, name))
        self.assertEqual(card.uid, f"urn:uuid:{person.uuid}")

    def test_round_trip_restores_extra_name_parts(self):
        person = make_person(
            extra_properties={"N": [{"value": "Doe;Jane;Marie;Dr.;PhD"}]}
        )
        text = serialize_cards([person_to_vcard(person)])
        self.assertIn("N:Doe;Jane;Marie;Dr.;PhD\r\n", text)
        (card,) = parse_vcards(text)
        self.assertEqual(card.fields["extra_properties"], person.extra_properties)

    def test_photo_round_trip(self):
        person = make_person()
        photo = tiny_jpeg()
        text = serialize_cards(
            [person_to_vcard(person, photo=photo, photo_type="jpeg")]
        )
        self.assertIn("PHOTO:data:image/jpeg;base64,", text)
        (card,) = parse_vcards(text)
        self.assertEqual(card.photo, photo)

    def test_group_card(self):
        alice = make_person(display_name="Alice")
        bob = make_person(display_name="Bob", import_uid="bob-uid")
        person_list = PersonList(uuid=uuid.uuid4(), name="Friends")
        text = serialize_cards([list_to_vcard(person_list, [alice, bob])])
        self.assertIn("KIND:group\r\n", text)
        self.assertIn("FN:Friends\r\n", text)
        self.assertIn(f"UID:urn:uuid:{person_list.uuid}\r\n", text)
        self.assertIn(f"MEMBER:urn:uuid:{alice.uuid}\r\n", text)
        self.assertIn("MEMBER:bob-uid\r\n", text)
        (card,) = parse_vcards(text)
        self.assertEqual(card.kind, "group")
        self.assertEqual(card.member_uids, [f"urn:uuid:{alice.uuid}", "bob-uid"])

    def test_empty_columns_emit_no_property(self):
        person = make_person(
            given_name="",
            family_name="",
            organization="",
            title="",
            birthday=None,
            emails=[],
            phones=[],
            addresses=[],
            notes="",
            extra_properties={},
        )
        text = serialize_cards([person_to_vcard(person)])
        for name in ("N", "ORG", "TITLE", "BDAY", "EMAIL", "TEL", "ADR", "NOTE"):
            self.assertNotIn("\r\n" + name, text)
