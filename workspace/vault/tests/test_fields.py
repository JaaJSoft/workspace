from django.test import SimpleTestCase

from workspace.vault.models import EntryField
from workspace.vault.services import fields


class QualifyFieldIdTests(SimpleTestCase):
    def test_a_reserved_identifier_passes_through(self):
        for field_id in ("username", "password", "totp", "uri"):
            self.assertEqual(fields.qualify_field_id(field_id), field_id)

    def test_an_entry_column_identifier_is_refused(self):
        """`name` and `notes` are carried by VaultEntry's own columns, which
        live in another table and so escape unique(entry, field_id)."""
        for field_id in ("name", "notes"):
            with self.assertRaises(ValueError):
                fields.qualify_field_id(field_id)

    def test_an_unprefixed_unknown_identifier_is_refused(self):
        with self.assertRaises(ValueError):
            fields.qualify_field_id("pin")

    def test_a_prefixed_identifier_keeps_its_prefix(self):
        self.assertEqual(fields.qualify_field_id("custom:pin"), "custom:pin")

    def test_a_custom_identifier_may_reuse_a_reserved_label(self):
        """custom:name and name are two different rows and two different
        associated data strings; collapsing them is the collision the prefix
        exists to prevent."""
        self.assertEqual(fields.qualify_field_id("custom:name"), "custom:name")
        self.assertNotEqual(
            fields.qualify_field_id("custom:name"), fields.qualify_field_id("username")
        )

    def test_a_malformed_custom_label_is_refused(self):
        for field_id in ("custom:", "custom:a:b", "custom:café", "custom: pin"):
            with self.assertRaises(ValueError):
                fields.qualify_field_id(field_id)

    def test_custom_field_id_builds_the_stored_identifier(self):
        self.assertEqual(fields.custom_field_id("pin"), "custom:pin")
        with self.assertRaises(ValueError):
            fields.custom_field_id("café")

    def test_a_stored_identifier_never_exceeds_the_column_it_goes_into(self):
        """EntryField.field_id is a CharField: SQLite ignores its length,
        PostgreSQL raises DataError, so an over-long label has to be refused
        here or it is a 500 in production and nowhere else."""
        longest = fields.custom_field_id("x" * fields.MAX_CUSTOM_LABEL)
        self.assertLessEqual(
            len(longest), EntryField._meta.get_field("field_id").max_length
        )
        with self.assertRaises(ValueError):
            fields.custom_field_id("x" * (fields.MAX_CUSTOM_LABEL + 1))
