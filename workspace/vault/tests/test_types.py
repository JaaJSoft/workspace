from django.test import SimpleTestCase, TestCase

from workspace.vault.models import EntryType, VaultEntry
from workspace.vault.types import Field, LoginEntry, as_typed, registry_for, schema_for


class RegistryTests(SimpleTestCase):
    def test_login_declares_only_reserved_identifiers(self):
        declared = {field.field_id for field in LoginEntry.FIELD_SCHEMA}
        self.assertEqual(declared, {"username", "password", "totp", "uri"})

    def test_every_registered_schema_declares_only_reserved_identifiers(self):
        """The guard that matters when a sixth type lands in v2: a schema that
        declares `name` would derive the associated data of encrypted_name."""
        for entry_type in EntryType.values:
            for field in schema_for(entry_type):
                self.assertIn(field.field_id, {"username", "password", "totp", "uri"})

    def test_a_schema_declaring_an_unreserved_identifier_is_refused(self):
        with self.assertRaises(ValueError):

            class BadEntry(LoginEntry):
                class Meta:
                    proxy = True

                FIELD_SCHEMA = (Field("pin", label="PIN"),)

    def test_registry_for_an_unknown_type_raises(self):
        with self.assertRaises(KeyError):
            registry_for("passport")


class AsTypedTests(TestCase):
    def test_as_typed_returns_the_proxy_for_the_row_type(self):
        entry = VaultEntry(type=EntryType.LOGIN)
        typed = as_typed(entry)
        self.assertIsInstance(typed, LoginEntry)
        self.assertEqual(typed.pk, entry.pk)

    def test_typed_manager_filters_on_its_type(self):
        self.assertEqual(
            LoginEntry.objects.all().query.where.children[0].rhs, EntryType.LOGIN
        )
