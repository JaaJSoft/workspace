"""The entry write service.

The payload builder is the whole trust chain in one function: what the server
verifies has to be what the server stores, never what the request said it was
storing.
"""

from django.test import TestCase

from workspace.vault.models import EntryField, EntryType, VaultEntry, VaultTag
from workspace.vault.services.entries import (
    EntryWriteError,
    entry_signature_payload,
    resolve_tags,
    write_entry,
)
from workspace.vault.tests.factories import make_account, make_vault


class EntrySignaturePayloadTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.vault = make_vault(self.user)
        self.tag_a = VaultTag.objects.create(
            vault=self.vault, encrypted_name="AQ", metadata_sig="AQ"
        )
        self.tag_b = VaultTag.objects.create(
            vault=self.vault, encrypted_name="Ag", metadata_sig="AQ"
        )

    def make_entry(self, **overrides):
        fields = {
            "vault": self.vault,
            "type": EntryType.LOGIN,
            "encrypted_name": "AQ",
            "metadata_sig": "AQ",
        }
        fields.update(overrides)
        return VaultEntry.objects.create(**fields)

    def test_the_payload_is_built_from_the_row_not_the_request(self):
        entry = self.make_entry(encrypted_name="AQ", is_favorite=True)
        entry.tags.set([self.tag_b, self.tag_a])
        EntryField.objects.create(
            entry=entry, field_id="password", encrypted_value="Ag"
        )
        EntryField.objects.create(
            entry=entry, field_id="username", encrypted_value="Aw"
        )

        payload = entry_signature_payload(
            entry,
            signer_account_uuid=self.identity.uuid,
            tag_uuids=[self.tag_b.uuid, self.tag_a.uuid],
            fields={"password": "Ag", "username": "Aw"},
        )
        self.assertEqual(
            payload["tags"],
            sorted([str(self.tag_a.uuid), str(self.tag_b.uuid)]),
        )
        self.assertEqual(payload["fields"], [["password", "Ag"], ["username", "Aw"]])
        self.assertEqual(payload["encrypted_name"], "AQ")
        self.assertTrue(payload["is_favorite"])
        self.assertNotIn("created_at", payload)
        self.assertNotIn("updated_at", payload)


class ResolveTagsTests(TestCase):
    def setUp(self):
        self.user, _, self.identity = make_account("owner")
        self.vault = make_vault(self.user)
        self.tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name="AQ", metadata_sig="AQ"
        )
        self.other_user, _, _ = make_account("stranger")
        self.other_vault = make_vault(self.other_user)
        self.other_tag = VaultTag.objects.create(
            vault=self.other_vault, encrypted_name="AQ", metadata_sig="AQ"
        )

    def test_a_tag_of_the_vault_resolves(self):
        self.assertEqual(
            resolve_tags(self.user, self.vault, [self.tag.uuid]), [self.tag]
        )

    def test_a_tag_from_another_vault_is_refused(self):
        with self.assertRaises(EntryWriteError):
            resolve_tags(self.user, self.vault, [self.other_tag.uuid])

    def test_a_tag_that_does_not_exist_is_refused_the_same_way(self):
        with self.assertRaises(EntryWriteError):
            resolve_tags(
                self.user, self.vault, ["0192f3a4-9999-7d8e-9f01-23456789abcd"]
            )


class WriteEntryTests(TestCase):
    def setUp(self):
        self.user, _, self.identity = make_account("owner")
        self.vault = make_vault(self.user)

    def _entry(self):
        return VaultEntry(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name="AQ",
            metadata_sig="AQ",
        )

    def test_the_field_set_is_replaced_wholesale(self):
        entry = write_entry(
            self._entry(), tags=[], fields={"password": "Ag", "totp": "Aw"}
        )
        write_entry(entry, tags=[], fields={"password": "Bg"})
        self.assertEqual(
            {field.field_id: field.encrypted_value for field in entry.fields.all()},
            {"password": "Bg"},
        )
