import datetime
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.people.models import SOURCE_IMPORT, SOURCE_MANUAL, Person, PersonList
from workspace.people.services.avatar import avatar_path, save_avatar
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person
from workspace.people.services.vcard import VCardError
from workspace.people.services.vcard_export import export_vcards
from workspace.people.services.vcard_import import import_vcards

User = get_user_model()
FIXTURES = Path(__file__).parent / "vcards"


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def tiny_png(width=6, height=4):
    buf = BytesIO()
    Image.new("RGB", (width, height), (20, 120, 220)).save(buf, format="PNG")
    return buf.getvalue()


def bomb_png():
    """A PNG whose declared size trips Pillow's decompression-bomb guard, a
    few KB on disk regardless (a 1-bit image compresses to nothing)."""
    buf = BytesIO()
    Image.new("1", (14000, 14000)).save(buf, format="PNG")
    return buf.getvalue()


def card(*lines):
    return "BEGIN:VCARD\nVERSION:4.0\n" + "\n".join(lines) + "\nEND:VCARD\n"


class ImportTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")

    def test_ios_file_creates_person_and_list(self):
        report = import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        self.assertEqual(report.as_dict(), {"created": 1, "updated": 0, "lists": 1})
        jane = Person.objects.get(owner=self.alice)
        self.assertEqual(jane.display_name, "Dr. Jane Marie Doe PhD")
        self.assertEqual(jane.source, SOURCE_IMPORT)
        self.assertEqual(jane.import_uid, "1B7C0F3A-2E5D-4C8B-9F1E-3A2B1C0D9E8F")
        self.assertEqual(jane.birthday, datetime.date(1985, 4, 12))
        self.assertIn("X-SOCIALPROFILE", jane.extra_properties)
        friends = PersonList.objects.get(owner=self.alice, name="Friends")
        # The second member uid names nobody: the list holds the one it found.
        self.assertEqual(list(friends.members.all()), [jane])

    def test_reimport_updates_instead_of_duplicating(self):
        import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        report = import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        self.assertEqual(report.as_dict(), {"created": 0, "updated": 1, "lists": 1})
        self.assertEqual(Person.objects.filter(owner=self.alice).count(), 1)
        friends = PersonList.objects.get(owner=self.alice, name="Friends")
        self.assertEqual(friends.members.count(), 1)

    def test_reimport_applies_the_new_values(self):
        import_vcards(card("UID:u1", "FN:Old Name", "TITLE:Dev"), owner=self.alice)
        import_vcards(card("UID:u1", "FN:New Name"), owner=self.alice)
        person = Person.objects.get(owner=self.alice)
        self.assertEqual(person.display_name, "New Name")
        self.assertEqual(person.title, "")

    def test_match_on_email_adopts_the_uid(self):
        existing = create_person(
            owner=self.alice,
            display_name="Jane",
            emails=[{"value": "Jane@Acme.example", "type": "work"}],
        )
        report = import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        self.assertEqual(report.created, 0)
        self.assertEqual(report.updated, 1)
        existing.refresh_from_db()
        self.assertEqual(existing.import_uid, "1B7C0F3A-2E5D-4C8B-9F1E-3A2B1C0D9E8F")
        self.assertEqual(existing.source, SOURCE_MANUAL)
        self.assertEqual(existing.display_name, "Dr. Jane Marie Doe PhD")

    def test_match_on_our_own_uuid_keeps_import_uid_empty(self):
        existing = create_person(owner=self.alice, display_name="Jane")
        report = import_vcards(
            card(f"UID:urn:uuid:{existing.uuid}", "FN:Jane Doe"), owner=self.alice
        )
        self.assertEqual(report.updated, 1)
        existing.refresh_from_db()
        self.assertEqual(existing.display_name, "Jane Doe")
        self.assertEqual(existing.import_uid, "")

    def test_unknown_urn_uuid_is_stored_as_import_uid(self):
        import_vcards(
            card("UID:urn:uuid:11111111-2222-4333-8444-555555555555", "FN:X"),
            owner=self.alice,
        )
        person = Person.objects.get(owner=self.alice)
        self.assertEqual(
            person.import_uid, "urn:uuid:11111111-2222-4333-8444-555555555555"
        )

    def test_scopes_do_not_share_matches(self):
        import_vcards(card("UID:u1", "FN:Shared"), owner=self.bob)
        report = import_vcards(card("UID:u1", "FN:Shared"), owner=self.alice)
        self.assertEqual(report.created, 1)
        self.assertEqual(Person.objects.filter(import_uid="u1").count(), 2)

    def test_group_scope(self):
        report = import_vcards(read_fixture("nextcloud.vcf"), group=self.team)
        self.assertEqual(report.as_dict(), {"created": 1, "updated": 0, "lists": 1})
        self.assertEqual(
            Person.objects.get(group=self.team).display_name, "Marie Curie"
        )
        physicists = PersonList.objects.get(group=self.team, name="Physicists")
        self.assertEqual(physicists.members.count(), 1)

    def test_one_scope_required(self):
        with self.assertRaises(ValueError):
            import_vcards(card("FN:X"))
        with self.assertRaises(ValueError):
            import_vcards(card("FN:X"), owner=self.alice, group=self.team)

    def test_long_group_name_is_cut_to_the_column(self):
        import_vcards(
            card("KIND:group", f"FN:{'g' * 300}") + card("FN:" + "p" * 300),
            owner=self.alice,
        )
        self.assertEqual(len(PersonList.objects.get(owner=self.alice).name), 255)
        self.assertEqual(len(Person.objects.get(owner=self.alice).display_name), 255)

    def test_garbage_raises_before_touching_the_database(self):
        with self.assertRaises(VCardError):
            import_vcards("nope", owner=self.alice)
        self.assertFalse(Person.objects.exists())

    def test_photo_becomes_the_avatar(self):
        import base64

        encoded = base64.b64encode(tiny_png()).decode()
        import_vcards(
            card("UID:p1", "FN:Pic", f"PHOTO:data:image/png;base64,{encoded}"),
            owner=self.alice,
        )
        person = Person.objects.get(owner=self.alice)
        self.assertTrue(person.has_avatar)
        self.assertTrue(default_storage.exists(avatar_path(person)))
        # A card without a photo leaves the avatar alone.
        import_vcards(card("UID:p1", "FN:Pic"), owner=self.alice)
        person.refresh_from_db()
        self.assertTrue(person.has_avatar)

    def test_broken_photo_is_ignored(self):
        import_vcards(
            card("UID:p1", "FN:Pic", "PHOTO;ENCODING=b:bm90IGFuIGltYWdl"),
            owner=self.alice,
        )
        person = Person.objects.get(owner=self.alice)
        self.assertFalse(person.has_avatar)

    def test_decompression_bomb_photo_is_ignored(self):
        import base64

        encoded = base64.b64encode(bomb_png()).decode()
        import_vcards(
            card("UID:p1", "FN:Pic", f"PHOTO:data:image/png;base64,{encoded}"),
            owner=self.alice,
        )
        person = Person.objects.get(owner=self.alice)
        self.assertFalse(person.has_avatar)

    def test_existing_list_gains_members_without_duplicates(self):
        friends = create_list(owner=self.alice, name="Friends")
        other = create_person(owner=self.alice, display_name="Other")
        add_members(friends, [other])
        import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        import_vcards(read_fixture("ios.vcf"), owner=self.alice)
        self.assertEqual(PersonList.objects.filter(owner=self.alice).count(), 1)
        self.assertEqual(friends.members.count(), 2)

    def test_members_resolve_against_the_address_book(self):
        existing = create_person(owner=self.alice, display_name="Already here")
        import_vcards(
            card("KIND:group", "FN:Crew", f"MEMBER:urn:uuid:{existing.uuid}"),
            owner=self.alice,
        )
        crew = PersonList.objects.get(owner=self.alice, name="Crew")
        self.assertEqual(list(crew.members.all()), [existing])

    def test_report_names_the_row_each_person_card_became(self):
        text = (
            card("UID:ann", "FN:Ann")
            + card("KIND:group", "FN:Team", "MEMBER:ann")
            + card("UID:bob", "FN:Bob")
        )
        report = import_vcards(text, owner=self.alice)
        self.assertEqual([p.display_name for p in report.persons], ["Ann", "Bob"])
        self.assertEqual(report.as_dict(), {"created": 2, "updated": 0, "lists": 1})

        again = import_vcards(text, owner=self.alice)
        self.assertEqual([p.pk for p in again.persons], [p.pk for p in report.persons])


class RoundTripTests(TestCase):
    """Export then import: nothing created, nothing changed."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.jane = create_person(
            owner=self.alice,
            display_name="Jane Doe",
            given_name="Jane",
            family_name="Doe",
            organization="ACME",
            title="CTO",
            birthday=datetime.date(1985, 4, 12),
            emails=[{"value": "jane@acme.example", "type": "work"}],
            phones=[{"value": "+33612345678", "type": "cell"}],
            addresses=[
                {
                    "street": "1 rue de la Paix",
                    "city": "Paris",
                    "region": "",
                    "postal_code": "75001",
                    "country": "France",
                    "type": "home",
                }
            ],
            notes="Cake; always, please.",
            extra_properties={"NICKNAME": [{"value": "JD"}]},
        )
        self.john = create_person(
            owner=self.alice, display_name="John", import_uid="google-42"
        )
        save_avatar(self.jane, BytesIO(tiny_png()), 0, 0, 4, 4)
        self.friends = create_list(owner=self.alice, name="Friends")
        add_members(self.friends, [self.jane, self.john])

    def snapshot(self):
        columns = (
            "uuid",
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
            "import_uid",
            "source",
            "has_avatar",
        )
        return sorted(
            Person.objects.filter(owner=self.alice).values(*columns),
            key=lambda row: row["display_name"],
        )

    def test_export_import_is_a_no_op(self):
        before = self.snapshot()
        text = export_vcards(
            Person.objects.filter(owner=self.alice),
            PersonList.objects.filter(owner=self.alice),
        )
        self.assertIn("PHOTO:data:image/webp;base64,", text)
        report = import_vcards(text, owner=self.alice)
        self.assertEqual(report.as_dict(), {"created": 0, "updated": 2, "lists": 1})
        self.assertEqual(self.snapshot(), before)
        self.friends.refresh_from_db()
        self.assertEqual(self.friends.members.count(), 2)

    def test_export_into_another_book_creates_everything_once(self):
        text = export_vcards(
            Person.objects.filter(owner=self.alice),
            PersonList.objects.filter(owner=self.alice),
        )
        bob = User.objects.create_user(username="bob", password="x")
        import_vcards(text, owner=bob)
        report = import_vcards(text, owner=bob)
        self.assertEqual(report.as_dict(), {"created": 0, "updated": 2, "lists": 1})
        self.assertEqual(Person.objects.filter(owner=bob).count(), 2)
        self.assertEqual(
            Person.objects.get(owner=bob, display_name="John").import_uid,
            "google-42",
        )
        self.assertEqual(
            Person.objects.get(owner=bob, display_name="Jane Doe").import_uid,
            f"urn:uuid:{self.jane.uuid}",
        )
        self.assertTrue(
            Person.objects.get(owner=bob, display_name="Jane Doe").has_avatar
        )
        self.assertEqual(PersonList.objects.get(owner=bob).members.count(), 2)
