from unittest import mock

from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import (
    EntryField,
    EntryType,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.services import migration
from workspace.vault.services.entries import entry_signature_payload
from workspace.vault.services.metadata import (
    folder_metadata_payload,
    tag_metadata_payload,
    vault_metadata_payload,
)
from workspace.vault.tests.factories import (
    HPKE_SUITE,
    make_account,
    make_key_wrap,
    make_vault,
    sealed,
    sign,
)

CURRENT_HPKE = {**HPKE_SUITE, "format": 2}
LIST_URL = "/api/v1/vault/migration"


class MigrationListTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        make_key_wrap(self.vault, self.user, hpke_suite=CURRENT_HPKE)

    def test_a_clean_account_answers_an_empty_list(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"vaults": []})

    def test_lists_a_trashed_stale_entry(self):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("n", 1),
            metadata_sig="AQ",
        )
        VaultEntry.objects.filter(pk=entry.pk).update(deleted_at=timezone.now())
        [listed] = self.client.get(LIST_URL).json()["vaults"]
        self.assertEqual(listed["entries"], [str(entry.uuid)])

    def test_no_identity_is_404(self):
        self.identity.delete()
        self.assertEqual(self.client.get(LIST_URL).status_code, 404)

    def test_anonymous_is_refused(self):
        self.client.logout()
        self.assertIn(self.client.get(LIST_URL).status_code, (302, 401, 403))

    def test_response_is_not_cached(self):
        self.assertIn("no-store", self.client.get(LIST_URL)["Cache-Control"])


class MigrateBatchTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(
            self.user, encrypted_name=sealed("v", 1), metadata_sig="AQ"
        )
        self.wrap = make_key_wrap(self.vault, self.user)
        self.url = f"/api/v1/vault/vaults/{self.vault.uuid}/migrate"

    def _post(self, items):
        return self.client.post(self.url, {"items": items}, "application/json")

    def _metadata_item(self, name=None, description=""):
        name = name or sealed("v2")
        payload = vault_metadata_payload(
            vault_uuid=self.vault.uuid,
            owner_account_uuid=self.identity.uuid,
            encrypted_name=name,
            encrypted_description=description,
            icon=self.vault.icon,
            color=self.vault.color,
            key_version=self.vault.key_version,
            is_favorite=self.vault.is_favorite,
        )
        return {
            "kind": "metadata",
            "encrypted_name": name,
            "encrypted_description": description,
            "metadata_sig": sign(self.signer, payload),
            "expected_sig": self.vault.metadata_sig,
        }

    def _entry(self, *, notes="", fields=None, name_format=1):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("n", name_format),
            encrypted_notes=notes,
            metadata_sig="AQ",
        )
        for field_id, value in (fields or {}).items():
            EntryField.objects.create(
                entry=entry, field_id=field_id, encrypted_value=value
            )
        return entry

    def _entry_item(self, entry, *, name=None, notes=None, fields=None):
        stored = dict(entry.fields.values_list("field_id", "encrypted_value"))
        new_fields = (
            fields if fields is not None else {key: sealed(key) for key in stored}
        )
        new_name = name or sealed("n2")
        new_notes = (
            notes
            if notes is not None
            else (sealed("notes2") if entry.encrypted_notes else "")
        )
        probe = VaultEntry.objects.get(pk=entry.pk)
        probe.encrypted_name, probe.encrypted_notes = new_name, new_notes
        payload = entry_signature_payload(
            probe,
            signer_account_uuid=self.identity.uuid,
            tag_uuids=list(entry.tags.values_list("uuid", flat=True)),
            fields=new_fields,
        )
        return {
            "kind": "entry",
            "uuid": str(entry.uuid),
            "encrypted_name": new_name,
            "encrypted_notes": new_notes,
            "fields": new_fields,
            "metadata_sig": sign(self.signer, payload),
            "expected_sig": entry.metadata_sig,
        }

    # --- acceptance ---------------------------------------------------------

    def test_metadata_and_wrap_migrate(self):
        before = Vault.objects.get(pk=self.vault.pk).updated_at
        response = self._post(
            [
                {
                    "kind": "wrap",
                    "wrapped_key": "bmV3",
                    "hpke_suite": CURRENT_HPKE,
                    "wrapped_key_expected": self.wrap.wrapped_key,
                },
                self._metadata_item(),
            ]
        )
        self.assertEqual(response.status_code, 204, response.content)
        vault = Vault.objects.get(pk=self.vault.pk)
        self.assertEqual(vault.updated_at, before)
        self.assertEqual(
            VaultKeyWrap.objects.get(pk=self.wrap.pk).hpke_suite["format"], 2
        )

    def test_folder_and_tag_migrate(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name=sealed("f", 1), metadata_sig="AQ"
        )
        tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name=sealed("t", 1), metadata_sig="AQ"
        )
        folder_name, tag_name = sealed("f2"), sealed("t2")
        folder_payload = folder_metadata_payload(
            folder_uuid=folder.uuid,
            vault_uuid=self.vault.uuid,
            signer_account_uuid=self.identity.uuid,
            parent_uuid=folder.parent_id,
            position=folder.position,
            encrypted_name=folder_name,
        )
        tag_payload = tag_metadata_payload(
            tag_uuid=tag.uuid,
            vault_uuid=self.vault.uuid,
            signer_account_uuid=self.identity.uuid,
            encrypted_name=tag_name,
            color=tag.color,
        )
        response = self._post(
            [
                {
                    "kind": "folder",
                    "uuid": str(folder.uuid),
                    "encrypted_name": folder_name,
                    "metadata_sig": sign(self.signer, folder_payload),
                    "expected_sig": "AQ",
                },
                {
                    "kind": "tag",
                    "uuid": str(tag.uuid),
                    "encrypted_name": tag_name,
                    "metadata_sig": sign(self.signer, tag_payload),
                    "expected_sig": "AQ",
                },
            ]
        )
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(
            VaultFolder.objects.get(pk=folder.pk).encrypted_name, folder_name
        )
        self.assertEqual(VaultTag.objects.get(pk=tag.pk).encrypted_name, tag_name)

    def test_entry_without_fields_or_notes_migrates(self):
        entry = self._entry()
        self.assertEqual(self._post([self._entry_item(entry)]).status_code, 204)

    def test_empty_description_stays_empty(self):
        item = self._metadata_item(description=sealed("filled"))
        self.assertEqual(self._post([item]).status_code, 400)

    def test_trashed_entry_migrates_and_stays_trashed(self):
        entry = self._entry()
        VaultEntry.objects.filter(pk=entry.pk).update(deleted_at=timezone.now())
        self.assertEqual(self._post([self._entry_item(entry)]).status_code, 204)
        self.assertIsNotNone(VaultEntry.objects.get(pk=entry.pk).deleted_at)

    def test_fields_are_updated_in_place(self):
        entry = self._entry(fields={"password": sealed("p", 1)})
        field_pk = entry.fields.get().pk
        self.assertEqual(self._post([self._entry_item(entry)]).status_code, 204)
        self.assertEqual(entry.fields.get().pk, field_pk)

    # --- refusals -----------------------------------------------------------

    def test_a_stale_precondition_is_409_naming_the_item(self):
        entry = self._entry()
        item = self._entry_item(entry)
        VaultEntry.objects.filter(pk=entry.pk).update(metadata_sig="Ag")
        response = self._post([item])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["uuid"], str(entry.uuid))

    def test_preconditions_are_checked_before_signatures(self):
        unsigned = self._entry()
        bad_item = self._entry_item(unsigned)
        bad_item["metadata_sig"] = sign(self.signer, {"v": 1, "type": "x"})
        stale = self._entry()
        stale_item = self._entry_item(stale)
        VaultEntry.objects.filter(pk=stale.pk).update(metadata_sig="Ag")
        response = self._post([bad_item, stale_item])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["uuid"], str(stale.uuid))

    def test_all_or_nothing(self):
        good = self._entry()
        bad = self._entry()
        bad_item = self._entry_item(bad)
        bad_item["metadata_sig"] = sign(self.signer, {"v": 1, "type": "x"})
        response = self._post([self._entry_item(good), bad_item])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            VaultEntry.objects.get(pk=good.pk).encrypted_name, good.encrypted_name
        )

    def test_a_conflict_during_the_writes_rolls_back_the_earlier_ones(self):
        first = self._entry()
        second = self._entry()
        items = [self._entry_item(first), self._entry_item(second)]
        real_check = migration._check

        def check_then_move_second(identity, vault, row, item):
            real_check(identity, vault, row, item)
            VaultEntry.objects.filter(pk=second.pk).update(metadata_sig="Ag")

        with mock.patch.object(migration, "_check", check_then_move_second):
            response = self._post(items)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            VaultEntry.objects.get(pk=first.pk).encrypted_name, first.encrypted_name
        )

    def test_a_row_of_another_vault_is_404_like_a_missing_one(self):
        other_user, _, _ = make_account("stranger")
        other_vault = make_vault(other_user)
        foreign = VaultEntry.objects.create(
            vault=other_vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("x", 1),
            metadata_sig="AQ",
        )
        item = {**self._entry_item(self._entry()), "uuid": str(foreign.uuid)}
        missing = {**item, "uuid": "00000000-0000-7000-8000-000000000000"}
        self.assertEqual(self._post([item]).status_code, 404)
        self.assertEqual(self._post([missing]).status_code, 404)
        self.assertEqual(self._post([item]).content, self._post([missing]).content)

    def test_metadata_needs_the_owner(self):
        member, member_signer, member_identity = make_account("member")
        make_key_wrap(self.vault, member)
        self.client.force_login(member)
        self.signer, self.identity = member_signer, member_identity
        self.assertEqual(self._post([self._metadata_item()]).status_code, 404)

    def test_wrap_of_another_recipient_is_404(self):
        member, _, _ = make_account("member")
        theirs = make_key_wrap(self.vault, member)
        item = {
            "kind": "wrap",
            "wrapped_key": "bmV3",
            "hpke_suite": CURRENT_HPKE,
            "wrapped_key_expected": theirs.wrapped_key,
        }
        self.wrap.delete()
        self.assertEqual(self._post([item]).status_code, 404)

    def test_a_plaintext_column_in_the_body_is_400(self):
        item = {**self._entry_item(self._entry()), "is_favorite": True}
        self.assertEqual(self._post([item]).status_code, 400)

    def test_adding_or_removing_a_field_is_400(self):
        entry = self._entry(fields={"password": sealed("p", 1)})
        self.assertEqual(
            self._post([self._entry_item(entry, fields={})]).status_code, 400
        )
        added = {"password": sealed("p"), "username": sealed("u")}
        self.assertEqual(
            self._post([self._entry_item(entry, fields=added)]).status_code, 400
        )

    def test_filling_empty_notes_is_400(self):
        entry = self._entry()
        self.assertEqual(
            self._post([self._entry_item(entry, notes=sealed("n"))]).status_code, 400
        )

    def test_a_superseded_ciphertext_is_400(self):
        entry = self._entry()
        item = self._entry_item(entry, name=sealed("n", 1))
        self.assertEqual(self._post([item]).status_code, 400)

    def _wrap_item(self, suite):
        return {
            "kind": "wrap",
            "wrapped_key": "bmV3",
            "hpke_suite": suite,
            "wrapped_key_expected": self.wrap.wrapped_key,
        }

    def test_a_loosely_typed_hpke_suite_is_400(self):
        self.assertEqual(
            self._post([self._wrap_item({**CURRENT_HPKE, "format": 2.0})]).status_code,
            400,
        )
        self.assertEqual(
            self._post([self._wrap_item({**CURRENT_HPKE, "kdf_id": True})]).status_code,
            400,
        )
        self.assertEqual(
            VaultKeyWrap.objects.get(pk=self.wrap.pk).hpke_suite, HPKE_SUITE
        )

    def test_more_than_the_ciphertext_cap_is_400(self):
        items = []
        for number in range(33):
            fields = {f"custom:f{index}": sealed("x") for index in range(64)}
            items.append(
                {
                    "kind": "entry",
                    "uuid": f"00000000-0000-7000-8000-{number:012d}",
                    "encrypted_name": sealed("n"),
                    "encrypted_notes": "",
                    "fields": fields,
                    "metadata_sig": "AQ",
                    "expected_sig": "AQ",
                }
            )
        response = self._post(items)
        self.assertEqual(response.status_code, 400)
        self.assertIn("too many ciphertexts", str(response.json()))

    def test_emptying_notes_is_400(self):
        entry = self._entry(notes=sealed("notes", 1))
        self.assertEqual(
            self._post([self._entry_item(entry, notes="")]).status_code, 400
        )

    def test_emptying_a_description_is_400(self):
        Vault.objects.filter(pk=self.vault.pk).update(
            encrypted_description=sealed("d", 1)
        )
        self.vault.refresh_from_db()
        self.assertEqual(self._post([self._metadata_item()]).status_code, 400)

    def test_an_entry_ciphertext_of_another_key_version_is_400(self):
        entry = self._entry()
        item = self._entry_item(entry, name=sealed("n", key_version=2))
        self.assertEqual(self._post([item]).status_code, 400)

    def test_a_folder_name_of_another_key_version_is_400(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name=sealed("f", 1), metadata_sig="AQ"
        )
        name = sealed("f2", key_version=2)
        payload = folder_metadata_payload(
            folder_uuid=folder.uuid,
            vault_uuid=self.vault.uuid,
            signer_account_uuid=self.identity.uuid,
            parent_uuid=None,
            position=folder.position,
            encrypted_name=name,
        )
        item = {
            "kind": "folder",
            "uuid": str(folder.uuid),
            "encrypted_name": name,
            "metadata_sig": sign(self.signer, payload),
            "expected_sig": "AQ",
        }
        self.assertEqual(self._post([item]).status_code, 400)

    def test_a_superseded_hpke_suite_is_400(self):
        item = {
            "kind": "wrap",
            "wrapped_key": "bmV3",
            "hpke_suite": HPKE_SUITE,
            "wrapped_key_expected": self.wrap.wrapped_key,
        }
        self.assertEqual(self._post([item]).status_code, 400)

    def test_a_signature_by_another_key_is_400(self):
        entry = self._entry()
        _, stranger_signer, _ = make_account("stranger")
        self.signer = stranger_signer
        self.assertEqual(self._post([self._entry_item(entry)]).status_code, 400)

    def test_caps(self):
        entry = self._entry()
        self.assertEqual(self._post([self._entry_item(entry)] * 201).status_code, 400)
        self.assertEqual(self._post([]).status_code, 400)

    def test_a_duplicate_item_is_400(self):
        entry = self._entry()
        item = self._entry_item(entry)
        self.assertEqual(self._post([item, item]).status_code, 400)
