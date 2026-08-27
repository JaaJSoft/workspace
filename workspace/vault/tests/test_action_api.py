"""The action endpoint.

Its one hard rule is that it answers nothing about existence: an entry in
someone else's vault and a UUID that names nothing must be indistinguishable,
which is where this endpoint deliberately parts company with the projects one
it is modelled on.
"""

import uuid

from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import EntryType, VaultEntry
from workspace.vault.tests.factories import make_account, make_vault

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
        copy of the projects one: a 404 here would confirm the row exists."""
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
        """A missing key would be a second channel saying the same thing a
        404 would."""
        absent = str(uuid.uuid4())
        response = self._post([str(self.entry.uuid), absent])
        self.assertEqual(set(response.json()), {str(self.entry.uuid), absent})

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

        few = [str(self._entry(self.vault, f"AQ{i}").uuid) for i in range(2)]
        count(few)  # warm whatever the first request caches
        baseline = count(few)
        many = few + [str(self._entry(self.vault, f"AR{i}").uuid) for i in range(20)]
        self.assertEqual(count(many), baseline)
