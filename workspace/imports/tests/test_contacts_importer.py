from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from workspace.imports.importers.base import (
    ImportContext,
    JobFailed,
    Outcome,
    importer_registry,
)
from workspace.imports.importers.contacts import (
    ContactsImporter,
    ContactsImportOptionsSerializer,
    categories,
    file_in_category_lists,
    inline_linked_photo,
    is_group_card,
    unfold,
)
from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem
from workspace.imports.providers.base import RemoteCard
from workspace.people.models import Person, PersonList
from workspace.people.services.lists import create_list
from workspace.people.services.persons import delete_person

from .fakes import FakeContactSource, fake_provider

User = get_user_model()
BOOK = "/contacts"


def vcard(uid, name, *lines):
    body = ["BEGIN:VCARD", "VERSION:3.0"]
    if uid:
        body.append(f"UID:{uid}")
    body += [f"FN:{name}", *lines, "END:VCARD"]
    return "\r\n".join(body) + "\r\n"


def card(uid, name, *lines, etag="e1", book=BOOK):
    return RemoteCard(
        id=f"{book}/{uid or name}.vcf", etag=etag, text=vcard(uid, name, *lines)
    )


def tiny_png():
    buf = BytesIO()
    Image.new("RGB", (8, 6), (20, 120, 220)).save(buf, format="PNG")
    return buf.getvalue()


class CardHelpersTests(SimpleTestCase):
    def test_unfold_joins_continuation_lines(self):
        self.assertEqual(unfold("NOTE:a\r\n  b\r\n\tc\r\nFN:x"), "NOTE:a bc\r\nFN:x")

    def test_group_cards_are_recognised_in_both_spellings(self):
        self.assertTrue(is_group_card("BEGIN:VCARD\nKIND:group\nEND:VCARD\n"))
        self.assertTrue(
            is_group_card("BEGIN:VCARD\r\nX-ADDRESSBOOKSERVER-KIND:Group\r\nEND:VCARD")
        )
        self.assertFalse(
            is_group_card("BEGIN:VCARD\nKIND:individual\nNOTE:KIND:group\nEND:VCARD")
        )


class ContactsImporterTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)
        self.provider = fake_provider()
        self.provider.cards = {BOOK: [card("ann", "Ann"), card("bob", "Bob")]}
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Nextcloud",
            base_url="https://x/dav",
            username="a",
        )
        self.importer = ContactsImporter()

    def tearDown(self):
        cache.clear()

    def _job(self, *books):
        # One live job per connection: the previous one is over by now.
        ImportJob.objects.filter(connection=self.conn).update(
            status=ImportJob.Status.COMPLETED
        )
        return ImportJob.objects.create(
            connection=self.conn,
            kinds=["contacts"],
            options={
                "contacts": {"books": list(books) or [{"id": BOOK, "target": "mine"}]}
            },
            status=ImportJob.Status.RUNNING,
        )

    def _run(self, job, deadline=None):
        ctx = ImportContext(job, self.provider, self.importer, deadline=deadline)
        return self.importer.run(ctx)

    def _names(self, **scope):
        return sorted(
            Person.objects.filter(**scope).values_list("display_name", flat=True)
        )

    def _items(self, job, status):
        return sorted(
            ImportJobItem.objects.filter(job=job, status=status).values_list(
                "remote_id", flat=True
            )
        )


class OptionsTests(ContactsImporterTestCase):
    def _validate(self, data):
        ser = ContactsImportOptionsSerializer(data=data, context={"owner": self.user})
        return ser.is_valid(), ser

    def test_a_book_defaults_to_the_personal_address_book(self):
        ok, ser = self._validate({"books": [{"id": "contacts/"}]})
        self.assertTrue(ok, ser.errors)
        self.assertEqual(
            ser.validated_data["books"], [{"id": "/contacts", "target": "mine"}]
        )

    def test_a_group_of_the_user_is_accepted(self):
        ok, ser = self._validate(
            {"books": [{"id": "/contacts", "target": f"group:{self.team.pk}"}]}
        )
        self.assertTrue(ok, ser.errors)

    def test_a_group_the_user_is_not_in_is_refused(self):
        other = Group.objects.create(name="Other")
        ok, ser = self._validate(
            {"books": [{"id": "/contacts", "target": f"group:{other.pk}"}]}
        )
        self.assertFalse(ok)
        self.assertIn("books", ser.errors)

    def test_no_book_the_root_dot_segments_and_duplicates_are_refused(self):
        for data in (
            {"books": []},
            {"books": [{"id": "/"}]},
            {"books": [{"id": "/contacts/../../files"}]},
            {"books": [{"id": "/contacts"}, {"id": "contacts"}]},
        ):
            with self.subTest(data=data):
                self.assertFalse(self._validate(data)[0])


class ContactsImporterTests(ContactsImporterTestCase):
    def test_registered_to_run_after_files(self):
        self.assertEqual(importer_registry.kinds(), ["files", "contacts"])

    def test_imports_every_card_into_the_personal_address_book(self):
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])
        stats = job.stats["contacts"]
        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["total_cards"], 2)
        self.assertEqual((stats["cards"], stats["created"], stats["books"]), (2, 2, 1))
        ann = Person.objects.get(owner=self.user, display_name="Ann")
        self.assertEqual(ann.import_uid, "ann")
        self.assertEqual(
            ImportJobItem.objects.get(
                job=job, remote_id="/contacts/ann.vcf"
            ).target_uuid,
            ann.uuid,
        )
        self.assertTrue(self.provider.last_contacts.closed)

    def test_each_book_lands_in_its_own_target(self):
        self.provider.cards["/team"] = [card("cid", "Carol", book="/team")]
        self._run(
            self._job(
                {"id": BOOK, "target": "mine"},
                {"id": "/team", "target": f"group:{self.team.pk}"},
            )
        )
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])
        self.assertEqual(self._names(group=self.team), ["Carol"])

    def test_cards_are_fetched_in_batches(self):
        self.provider.cards[BOOK] = [card(f"u{i}", f"P{i}") for i in range(120)]
        self._run(self._job())
        self.assertEqual(
            [len(ids) for ids in self.provider.last_contacts.fetch_calls], [50, 50, 20]
        )
        self.assertEqual(Person.objects.filter(owner=self.user).count(), 120)

    def test_a_second_run_only_fetches_what_changed(self):
        self._run(self._job())
        self.provider.cards[BOOK] = [
            card("ann", "Ann"),
            card("bob", "Robert", etag="e2"),
            card("cid", "Carol"),
        ]
        job = self._job()
        self._run(job)
        self.assertEqual(
            self.provider.last_contacts.fetch_calls,
            [["/contacts/bob.vcf", "/contacts/cid.vcf"]],
        )
        stats = job.stats["contacts"]
        self.assertEqual(
            (stats["unchanged"], stats["updated"], stats["created"]), (1, 1, 1)
        )
        self.assertEqual(self._names(owner=self.user), ["Ann", "Carol", "Robert"])

    def test_a_locally_deleted_contact_is_imported_again(self):
        self._run(self._job())
        delete_person(Person.objects.get(owner=self.user, display_name="Ann"))
        self._run(self._job())
        self.assertEqual(
            self.provider.last_contacts.fetch_calls, [["/contacts/ann.vcf"]]
        )
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])

    def test_a_card_without_uid_is_not_fetched_again_while_unchanged(self):
        self.provider.cards[BOOK] = [card("", "Nobody", "EMAIL:n@example.org")]
        self._run(self._job())
        self._run(self._job())
        self.assertEqual(self.provider.last_contacts.fetch_calls, [])
        self.assertEqual(Person.objects.filter(owner=self.user).count(), 1)

    def test_a_resource_holding_two_cards_imports_both_and_points_at_the_first(self):
        self.provider.cards[BOOK] = [
            RemoteCard(
                id=f"{BOOK}/pair.vcf",
                etag="e1",
                text=vcard("p1", "First") + vcard("p2", "Second"),
            )
        ]
        job = self._job()
        self._run(job)
        self.assertEqual(self._names(owner=self.user), ["First", "Second"])
        self.assertEqual(
            ImportJobItem.objects.get(job=job).target_uuid,
            Person.objects.get(display_name="First").uuid,
        )

    def test_an_empty_address_book_completes_with_nothing_to_import(self):
        self.provider.cards[BOOK] = []
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(
            self.importer.summarize(job.stats["contacts"]), "Nothing to import."
        )

    def test_a_malformed_card_fails_alone(self):
        self.provider.cards[BOOK].insert(
            1,
            RemoteCard(
                id=f"{BOOK}/bad.vcf", etag="e9", text="BEGIN:VCARD\r\nFN:Broken\r\n"
            ),
        )
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])
        item = ImportJobItem.objects.get(job=job, remote_id="/contacts/bad.vcf")
        self.assertEqual(item.status, ImportJobItem.Status.FAILED)
        self.assertEqual(item.error, "This contact is not a valid vCard.")
        self.assertEqual(job.stats["contacts"]["failed"], 1)

    def test_a_card_gone_between_listing_and_fetch_is_reported(self):
        self.provider.vanished = {"/contacts/bob.vcf"}
        job = self._job()
        self._run(job)
        self.assertEqual(
            self._items(job, ImportJobItem.Status.FAILED), ["/contacts/bob.vcf"]
        )
        self.assertEqual(self._names(owner=self.user), ["Ann"])

    def test_a_photo_fetch_that_raises_never_fails_the_card(self):
        url = "https://x/remote.php/dav/addressbooks/users/a/contacts/ann.vcf?photo"
        self.provider.cards[BOOK] = [card("ann", "Ann", f"PHOTO:{url}")]
        job = self._job()
        with patch.object(
            FakeContactSource, "fetch_photo", side_effect=RuntimeError("boom")
        ):
            self.assertIs(self._run(job), Outcome.DONE)
        ann = Person.objects.get(display_name="Ann")
        self.assertFalse(ann.has_avatar)
        self.assertEqual(
            self._items(job, ImportJobItem.Status.DONE), ["/contacts/ann.vcf"]
        )

    def test_a_resumed_slice_does_not_recount_a_failed_card(self):
        self.provider.cards[BOOK].insert(
            0,
            RemoteCard(
                id=f"{BOOK}/bad.vcf", etag="e9", text="BEGIN:VCARD\r\nFN:Broken\r\n"
            ),
        )
        job = self._job()

        def out_after_first_failure(ctx):
            return ImportJobItem.objects.filter(
                job=ctx.job, status=ImportJobItem.Status.FAILED
            ).exists()

        with patch.object(ImportContext, "out_of_time", out_after_first_failure):
            self.assertIs(self._run(job), Outcome.PAUSED)
        self.assertEqual(job.stats["contacts"]["failed"], 1)

        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["contacts"]["failed"], 1)
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])
        self.assertEqual(
            self.provider.last_contacts.fetch_calls,
            [["/contacts/ann.vcf", "/contacts/bob.vcf"]],
        )

    def test_a_failed_fetch_marks_its_batch_and_the_import_goes_on(self):
        self.provider.cards["/team"] = [card("cid", "Carol", book="/team")]
        self.provider.fail_fetch = {"/contacts/ann.vcf"}
        job = self._job(
            {"id": BOOK, "target": "mine"}, {"id": "/team", "target": "mine"}
        )
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(
            self._items(job, ImportJobItem.Status.FAILED),
            ["/contacts/ann.vcf", "/contacts/bob.vcf"],
        )
        self.assertEqual(self._names(owner=self.user), ["Carol"])

    @override_settings(IMPORTS_MAX_CONSECUTIVE_ERRORS=2)
    def test_too_many_consecutive_fetch_failures_fail_the_job(self):
        self.provider.cards[BOOK] = [card(f"u{i}", f"P{i}") for i in range(120)]
        self.provider.fail_fetch = {"/contacts/u0.vcf", "/contacts/u50.vcf"}
        with self.assertRaisesMessage(JobFailed, "2 consecutive errors"):
            self._run(self._job())

    def test_an_unlistable_book_fails_the_job_while_planning(self):
        self.provider.fail_refs = {BOOK}
        with self.assertRaisesMessage(
            JobFailed, "Could not list the address book '/contacts'"
        ):
            self._run(self._job())

    def test_a_book_that_can_no_longer_be_listed_is_reported_and_skipped(self):
        self.provider.cards["/team"] = [card("cid", "Carol", book="/team")]
        job = self._job(
            {"id": BOOK, "target": "mine"}, {"id": "/team", "target": "mine"}
        )
        job.stats["contacts"] = {"planned": True}
        self.provider.fail_refs = {BOOK}
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(self._items(job, ImportJobItem.Status.FAILED), ["/contacts"])
        self.assertEqual(self._names(owner=self.user), ["Carol"])

    def test_leaving_the_target_group_stops_the_import_before_any_write(self):
        job = self._job({"id": BOOK, "target": f"group:{self.team.pk}"})
        self.user.groups.remove(self.team)
        with self.assertRaisesMessage(JobFailed, "no longer a member"):
            self._run(job)
        self.assertFalse(Person.objects.exists())
        self.assertIsNone(self.provider.last_contacts)

    def test_group_cards_are_imported_after_the_people_they_list(self):
        self.provider.cards[BOOK].insert(
            0,
            RemoteCard(
                id=f"{BOOK}/team.vcf",
                etag="g1",
                text=vcard("team", "Friends", "KIND:group", "MEMBER:ann", "MEMBER:bob"),
            ),
        )
        job = self._job()
        self._run(job)
        friends = PersonList.objects.get(owner=self.user, name="Friends")
        self.assertEqual(
            sorted(friends.members.values_list("display_name", flat=True)),
            ["Ann", "Bob"],
        )
        self.assertEqual(job.stats["contacts"]["lists"], 1)
        self.assertEqual(job.stats["contacts"]["cards"], 3)

    def test_a_slice_out_of_time_resumes_mid_book_without_duplicates(self):
        job = self._job()
        user = self.user

        def out_after_first_contact(ctx):
            return Person.objects.filter(owner=user).exists()

        with patch.object(ImportContext, "out_of_time", out_after_first_contact):
            self.assertIs(self._run(job), Outcome.PAUSED)
        self.assertEqual(self._names(owner=self.user), ["Ann"])
        self.assertEqual(job.stats["contacts"]["phase"], "importing")

        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(self._names(owner=self.user), ["Ann", "Bob"])
        self.assertEqual(
            self.provider.last_contacts.fetch_calls, [["/contacts/bob.vcf"]]
        )

    def test_a_deadline_during_listing_pauses_before_anything_is_imported(self):
        job = self._job()
        self.assertIs(
            self._run(job, deadline=timezone.now() - timedelta(seconds=1)),
            Outcome.PAUSED,
        )
        self.assertEqual(job.stats["contacts"]["phase"], "listing")
        self.assertFalse(Person.objects.exists())
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["contacts"]["total_cards"], 2)

    def test_cancellation_stops_between_cards_and_keeps_what_was_done(self):
        job = self._job()
        user = self.user
        with patch.object(
            ImportContext,
            "cancelled",
            lambda ctx: Person.objects.filter(owner=user).exists(),
        ):
            self.assertIs(self._run(job), Outcome.CANCELLED)
        self.assertEqual(self._names(owner=self.user), ["Ann"])
        self.assertEqual(
            self._items(job, ImportJobItem.Status.DONE), ["/contacts/ann.vcf"]
        )

    def test_summary_names_the_non_zero_counts(self):
        self.assertEqual(
            self.importer.summarize(
                {"created": 3, "updated": 1, "unchanged": 0, "lists": 2, "failed": 1}
            ),
            "3 contacts added, 1 updated, 2 lists, 1 failed",
        )


class CategoriesTests(ContactsImporterTestCase):
    def _lists(self, **scope):
        return {
            person_list.name: sorted(
                person_list.members.values_list("display_name", flat=True)
            )
            for person_list in PersonList.objects.filter(**scope)
        }

    def test_categories_become_lists_of_the_target_address_book(self):
        self.provider.cards[BOOK] = [
            card("ann", "Ann", "CATEGORIES:Friends,Family"),
            card("bob", "Bob", "CATEGORIES:Friends"),
        ]
        job = self._job()
        self._run(job)
        self.assertEqual(
            self._lists(owner=self.user),
            {"Family": ["Ann"], "Friends": ["Ann", "Bob"]},
        )
        self.assertEqual(job.stats["contacts"]["lists"], 2)

    def test_lists_go_to_the_group_when_the_book_does(self):
        self.provider.cards[BOOK] = [card("ann", "Ann", "CATEGORIES:Clients")]
        self._run(self._job({"id": BOOK, "target": f"group:{self.team.pk}"}))
        self.assertEqual(self._lists(group=self.team), {"Clients": ["Ann"]})
        self.assertEqual(self._lists(owner=self.user), {})

    def test_an_escaped_comma_stays_in_the_name(self):
        self.provider.cards[BOOK] = [
            card("ann", "Ann", r"CATEGORIES:Smith\, Jones,VIP")
        ]
        self._run(self._job())
        self.assertEqual(sorted(self._lists(owner=self.user)), ["Smith, Jones", "VIP"])

    def test_an_existing_list_of_the_same_name_is_reused(self):
        create_list(owner=self.user, name="Friends")
        self.provider.cards[BOOK] = [card("ann", "Ann", "CATEGORIES:Friends")]
        job = self._job()
        self._run(job)
        self.assertEqual(self._lists(owner=self.user), {"Friends": ["Ann"]})
        self.assertEqual(job.stats["contacts"]["lists"], 0)

    def test_a_re_run_does_not_duplicate_the_lists(self):
        self.provider.cards[BOOK] = [card("ann", "Ann", "CATEGORIES:Friends")]
        self._run(self._job())
        self.provider.cards[BOOK] = [
            card("ann", "Ann", "CATEGORIES:Friends", etag="e2")
        ]
        job = self._job()
        self._run(job)
        self.assertEqual(PersonList.objects.filter(name="Friends").count(), 1)
        self.assertEqual(job.stats["contacts"]["lists"], 0)

    def test_categories_read_every_line_once(self):
        person = Person(
            extra_properties={
                "CATEGORIES": [{"value": "A,B"}, {"value": "B, C\\nD"}, {"value": ""}]
            }
        )
        self.assertEqual(categories(person), ["A", "B", "C D"])

    def test_concurrent_list_creation_reuses_the_created_list(self):
        person = Person.objects.create(
            owner=self.user,
            display_name="Ann",
            extra_properties={"CATEGORIES": [{"value": "Friends"}]},
        )
        scope = {"owner": self.user}
        create_list(owner=self.user, name="Friends")

        # Simulates another worker's insert landing between the lookup and
        # the insert: filter().first() still returns None, and create_list
        # raises the unique violation the real one would hit.
        original_filter = PersonList.objects.filter

        def filter_returns_none(*args, **kwargs):
            qs = original_filter(*args, **kwargs)
            qs.first = lambda: None
            return qs

        def create_list_raises(*args, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        with patch.object(
            PersonList.objects, "filter", side_effect=filter_returns_none
        ):
            with patch(
                "workspace.imports.importers.contacts.create_list",
                side_effect=create_list_raises,
            ):
                created = file_in_category_lists(person, scope)

        self.assertEqual(created, 0)
        self.assertEqual(
            PersonList.objects.filter(owner=self.user, name="Friends").count(), 1
        )
        friends = PersonList.objects.get(owner=self.user, name="Friends")
        self.assertIn(person, friends.members.all())


class InlineLinkedPhotoTests(SimpleTestCase):
    def test_a_folded_url_is_found_and_replaced_by_the_image(self):
        seen = []
        text = (
            "BEGIN:VCARD\r\nPHOTO;VALUE=uri:https://x/very/long\r\n /path.png\r\n"
            "END:VCARD\r\n"
        )
        out = inline_linked_photo(
            text, lambda url: seen.append(url) or (b"\x89PNG", "png")
        )
        self.assertEqual(seen, ["https://x/very/long/path.png"])
        self.assertIn("\r\nPHOTO:data:image/png;base64,iVBORw==\r\nEND:VCARD", out)

    def test_an_inline_photo_is_left_alone(self):
        text = "BEGIN:VCARD\r\nPHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ\r\nEND:VCARD\r\n"
        self.assertEqual(
            inline_linked_photo(text, lambda url: self.fail("fetched")), text
        )

    def test_a_photo_that_cannot_be_fetched_leaves_the_card_as_it_was(self):
        text = "BEGIN:VCARD\r\nPHOTO:https://x/p.png\r\nEND:VCARD\r\n"
        self.assertEqual(inline_linked_photo(text, lambda url: None), text)

    def test_an_undecodable_subtype_is_not_inlined(self):
        text = "BEGIN:VCARD\r\nPHOTO:https://x/p.svg\r\nEND:VCARD\r\n"
        self.assertEqual(
            inline_linked_photo(text, lambda url: (b"<svg/>", "svg+xml")), text
        )


class LinkedPhotoTests(ContactsImporterTestCase):
    URL = "https://x/remote.php/dav/addressbooks/users/a/contacts/ann.vcf?photo"

    def test_a_linked_photo_becomes_the_avatar(self):
        self.provider.photos = {self.URL: (tiny_png(), "png")}
        self.provider.cards[BOOK] = [card("ann", "Ann", f"PHOTO;VALUE=uri:{self.URL}")]
        self._run(self._job())
        ann = Person.objects.get(display_name="Ann")
        self.assertTrue(ann.has_avatar)
        self.assertNotIn("PHOTO", ann.extra_properties)
        self.assertEqual(self.provider.last_contacts.photo_calls, [self.URL])

    def test_a_photo_that_cannot_be_fetched_keeps_its_link(self):
        self.provider.cards[BOOK] = [card("ann", "Ann", f"PHOTO:{self.URL}")]
        self._run(self._job())
        ann = Person.objects.get(display_name="Ann")
        self.assertFalse(ann.has_avatar)
        self.assertEqual(ann.extra_properties["PHOTO"][0]["value"], self.URL)
