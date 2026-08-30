"""The action endpoint.

Its one hard rule is that a batch survives its worst member: an entry in
someone else's vault and a UUID that names nothing both come back as an
empty list under the key that was submitted, so the other 199 answers stand
and the client reads every key it sent. That is where this endpoint parts
company with the projects one it is modelled on, which 404s the whole batch.
"""

import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.vault.models import EntryField, EntryType, VaultEntry
from workspace.vault.tests.factories import make_account, make_key_wrap, make_vault

URL = "/api/v1/vault/actions"


class ActionApiTests(TestCase):
    def setUp(self):
        self.user, _, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        self.entry = self._entry(self.vault, "AQID")

        self.other_user, _, _ = make_account("stranger")
        self.other_vault = make_vault(self.other_user)
        self.other_entry = self._entry(self.other_vault, "AQIE")

    def _entry(self, vault, name):
        return VaultEntry.objects.create(
            vault=vault,
            type=EntryType.LOGIN,
            encrypted_name=name,
            metadata_sig="AQ",
        )

    def _post(self, uuids):
        return self.client.post(URL, {"uuids": uuids}, "application/json")

    def test_an_own_entry_gets_its_actions(self):
        response = self._post([str(self.entry.uuid)])
        self.assertEqual(response.status_code, 200)
        ids = [action["id"] for action in response.json()[str(self.entry.uuid)]]
        self.assertIn("edit", ids)
        self.assertIn("trash", ids)

    def test_an_entry_in_another_vault_gets_an_empty_list_not_an_error(self):
        """The acceptance criterion, and the reason this endpoint is not a
        copy of the projects one: a 404 here would take the whole batch
        down over a UUID the caller may simply have gone stale on."""
        response = self._post([str(self.other_entry.uuid)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {str(self.other_entry.uuid): []})

    def test_a_uuid_that_names_nothing_answers_exactly_the_same(self):
        absent = str(uuid.uuid4())
        theirs = str(self.other_entry.uuid)
        response = self._post([absent, theirs])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {absent: [], theirs: []})

    def test_every_submitted_uuid_gets_a_key(self):
        """A missing key would force every client to check for holes."""
        absent = str(uuid.uuid4())
        response = self._post([str(self.entry.uuid), absent])
        self.assertEqual(set(response.json()), {str(self.entry.uuid), absent})

    def test_a_uuid_comes_back_under_the_spelling_it_was_sent_in(self):
        """UUIDs have several valid spellings and the client reads
        data[whatItSent]. Keying by the canonical form silently loses every
        caller that sent an uppercase or braced one."""
        submitted = str(self.entry.uuid).upper()
        response = self._post([submitted])
        self.assertEqual(response.status_code, 200)
        self.assertIn(submitted, response.json())
        self.assertTrue(response.json()[submitted])

    def test_two_spellings_of_one_uuid_each_get_their_key(self):
        lower = str(self.entry.uuid)
        response = self._post([lower, lower.upper()])
        body = response.json()
        self.assertEqual(set(body), {lower, lower.upper()})
        self.assertEqual(body[lower], body[lower.upper()])

    def test_a_body_whose_top_level_is_not_an_object_answers_400(self):
        """A JSON array or scalar hands the view a list or an int, and
        reading a key off it would be a 500 where the schema says 400."""
        for body in ([str(self.entry.uuid)], 42, "nope"):
            with self.subTest(body=body):
                response = self.client.post(URL, body, "application/json")
                self.assertEqual(response.status_code, 400)

    def test_a_row_of_an_unknown_type_does_not_take_the_batch_down(self):
        """type is a Python-side choice, so a fixture or a migration can put
        a value in the column that no proxy claims. One such row must not
        cost the other entries in the batch their answer."""
        VaultEntry.objects.filter(pk=self.entry.pk).update(type="ghost")
        healthy = self._entry(self.vault, "AQIF")
        response = self._post([str(self.entry.uuid), str(healthy.uuid)])
        self.assertEqual(response.status_code, 200)
        ids = [a["id"] for a in response.json()[str(self.entry.uuid)]]
        self.assertIn("edit", ids)
        self.assertNotIn("copy_password", ids)
        self.assertTrue(response.json()[str(healthy.uuid)])

    def test_a_trashed_entry_gets_the_trash_actions(self):
        self.entry.deleted_at = timezone.now()
        self.entry.save(update_fields=["deleted_at"])
        ids = [
            action["id"]
            for action in self._post([str(self.entry.uuid)]).json()[
                str(self.entry.uuid)
            ]
        ]
        self.assertEqual(sorted(ids), ["delete_forever", "restore"])

    def test_a_malformed_uuid_answers_400(self):
        response = self._post(["not-a-uuid"])
        self.assertEqual(response.status_code, 400)

    def test_an_empty_list_answers_400(self):
        self.assertEqual(self._post([]).status_code, 400)

    def test_a_body_that_is_not_a_list_answers_400(self):
        response = self.client.post(URL, {"uuids": "nope"}, "application/json")
        self.assertEqual(response.status_code, 400)

    def test_a_batch_above_the_cap_is_refused_not_truncated(self):
        response = self._post([str(uuid.uuid4()) for _ in range(201)])
        self.assertEqual(response.status_code, 400)

    def test_a_duplicate_uuid_is_answered_once(self):
        response = self._post([str(self.entry.uuid)] * 3)
        self.assertEqual(len(response.json()), 1)

    def test_the_response_is_never_stored_by_a_cache(self):
        response = self._post([str(self.entry.uuid)])
        self.assertIn("no-store", response["Cache-Control"])

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self._post([str(self.entry.uuid)])
        self.assertIn(response.status_code, (302, 403))

    def test_the_batch_does_not_grow_a_query_per_entry(self):
        """Two measurements rather than one pinned number: the absolute count
        moves with whatever Django has already cached in the process, so only
        its invariance under a growing batch means anything."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count(uuids):
            with CaptureQueriesContext(connection) as captured:
                self._post(uuids)
            return len(captured.captured_queries)

        few = [str(self._entry(self._member_vault(0), "AQ0").uuid)]
        count(few)  # warm whatever the first request caches
        baseline = count(few)
        many = few + [
            str(self._entry(self._member_vault(i), f"AR{i}").uuid) for i in range(1, 20)
        ]
        self.assertEqual(count(many), baseline)

    def _member_vault(self, index):
        """A vault the caller reaches through a wrap, not by owning it.

        An owned vault's role is decidable from the row already loaded, so
        only these make the query count guard the member path."""
        vault = make_vault(self.other_user, encrypted_name=f"AQ{index:02d}")
        make_key_wrap(vault, self.user)
        return vault

    def test_a_login_entry_without_a_key_is_not_offered_the_totp_action(self):
        """The type declares a totp field; this row does not carry one. The
        menu must not offer a copy of a code that does not exist."""
        EntryField.objects.create(
            entry=self.entry, field_id="password", encrypted_value="AQID"
        )
        response = self._post([str(self.entry.uuid)])
        ids = [action["id"] for action in response.json()[str(self.entry.uuid)]]
        self.assertIn("copy_password", ids)
        self.assertNotIn("copy_totp", ids)

    def test_the_totp_action_appears_once_the_row_carries_a_key(self):
        EntryField.objects.create(
            entry=self.entry, field_id="totp", encrypted_value="AQID"
        )
        response = self._post([str(self.entry.uuid)])
        ids = [action["id"] for action in response.json()[str(self.entry.uuid)]]
        self.assertIn("copy_totp", ids)

    def test_the_field_lookup_does_not_cost_a_query_per_entry(self):
        """The prefetch is the point: without it a batch of 200 entries would
        issue 200 extra queries, which is the shape the registry's purity
        rule exists to keep out.

        Asserted as an invariant rather than as a number - a fixed count
        passes for the wrong reason the day an unrelated query is added or
        removed, while "growing the batch does not grow the query count"
        fails only for the defect it names.
        """
        rows = [self._entry(self.vault, f"AQI{n}") for n in range(3)]
        for row in [self.entry, *rows]:
            EntryField.objects.create(
                entry=row, field_id="totp", encrypted_value="AQID"
            )

        with CaptureQueriesContext(connection) as small:
            self._post([str(self.entry.uuid)])
        with CaptureQueriesContext(connection) as large:
            self._post([str(row.uuid) for row in [self.entry, *rows]])

        self.assertEqual(len(large), len(small))


class VaultTargetActionApiTests(TestCase):
    """The same endpoint, asked about vaults rather than entries."""

    def setUp(self):
        self.user, _, _ = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)

        self.other_user, _, _ = make_account("stranger")
        self.other_vault = make_vault(self.other_user)

        # A vault the user can open without owning: a key wrap makes them a
        # member, which is the role every vault action refuses.
        self.shared = make_vault(self.other_user)
        make_key_wrap(self.shared, self.user)

    def _post(self, uuids, target="vault"):
        return self.client.post(
            URL, {"uuids": uuids, "target": target}, "application/json"
        )

    def test_the_owner_gets_the_vault_action_set(self):
        response = self._post([str(self.vault.uuid)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [action["id"] for action in response.json()[str(self.vault.uuid)]],
            ["rename", "set_appearance", "favorite", "unfavorite", "delete"],
        )

    def test_a_member_gets_a_key_and_an_empty_list(self):
        """Reachable and un-actionable are different answers from absent: the
        vault opens, and nothing about it may be rewritten."""
        response = self._post([str(self.shared.uuid)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[str(self.shared.uuid)], [])

    def test_an_unreachable_vault_answers_like_one_that_does_not_exist(self):
        response = self._post([str(self.other_vault.uuid), str(uuid.uuid4())])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[str(self.other_vault.uuid)], [])

    def test_an_unknown_target_is_refused(self):
        response = self._post([str(self.vault.uuid)], target="folder")
        self.assertEqual(response.status_code, 400)

    def test_a_target_that_is_not_a_string_is_refused_rather_than_crashing(self):
        """A JSON array or object is unhashable, so a set membership test on
        it raises rather than answering - and a malformed body deserves the
        same 400 as a misspelt one, not a 500."""
        for target in ([], {}, 3, None):
            with self.subTest(target=target):
                response = self._post([str(self.vault.uuid)], target=target)
                self.assertEqual(response.status_code, 400)

    def test_the_default_target_is_still_the_entry_registry(self):
        """Omitting the field must keep the shape every existing caller
        already sends, or the browser's entry menus break silently."""
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name="AQID",
            metadata_sig="AQ",
        )
        response = self.client.post(
            URL, {"uuids": [str(entry.uuid)]}, "application/json"
        )
        ids = [action["id"] for action in response.json()[str(entry.uuid)]]
        self.assertIn("trash", ids)

    def test_a_vault_uuid_asked_for_as_an_entry_gets_an_empty_list(self):
        """The two namespaces do not overlap, and the endpoint must not fall
        back from one to the other: a vault is not an entry, so asking about
        it under the wrong target is the same as asking about nothing."""
        response = self._post([str(self.vault.uuid)], target="entry")
        self.assertEqual(response.json()[str(self.vault.uuid)], [])
