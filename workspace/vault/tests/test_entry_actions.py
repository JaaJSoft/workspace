"""The real action set.

What is pinned here is the part a client would otherwise guess: which actions
depend on the entry's type, which survive the trash, and which need
ownership. The registry's machinery is tested in test_actions.py.
"""

from django.test import SimpleTestCase

from workspace.vault.actions import VaultActionRegistry
from workspace.vault.models import EntryType, VaultRole
from workspace.vault.types import Field, LoginEntry, schema_for


class _Entry:
    type = EntryType.LOGIN


class EntryActionTests(SimpleTestCase):
    def _ids(self, *, role=VaultRole.OWNER, trashed=False, schema=None):
        return [
            action["id"]
            for action in VaultActionRegistry.get_available_actions(
                None,
                _Entry(),
                role=role,
                trashed=trashed,
                schema=LoginEntry.FIELD_SCHEMA if schema is None else schema,
            )
        ]

    def test_a_login_entry_offers_the_everyday_actions(self):
        ids = self._ids()
        for expected in ("edit", "copy_username", "copy_password", "move", "trash"):
            self.assertIn(expected, ids)

    def test_the_trash_swaps_the_set_rather_than_emptying_it(self):
        ids = self._ids(trashed=True)
        self.assertEqual(sorted(ids), ["delete_forever", "restore"])

    def test_restore_is_offered_only_from_the_trash(self):
        self.assertNotIn("restore", self._ids(trashed=False))

    def test_a_type_without_a_totp_field_is_not_offered_the_totp_action(self):
        """The schema decides, not the type name: a second type that also
        declares totp must get the action without this file being edited."""
        without_totp = tuple(
            field for field in LoginEntry.FIELD_SCHEMA if field.field_id != "totp"
        )
        self.assertNotIn("copy_totp", self._ids(schema=without_totp))
        self.assertIn("copy_totp", self._ids())

    def test_a_type_without_a_uri_field_is_not_offered_the_open_action(self):
        without_uri = tuple(
            field for field in LoginEntry.FIELD_SCHEMA if field.field_id != "uri"
        )
        self.assertNotIn("open_uri", self._ids(schema=without_uri))

    def test_an_invented_field_is_enough_to_earn_its_action(self):
        """The other direction of the same claim, so the test cannot pass by
        the action simply never being offered."""
        invented = (Field("totp", label="Code"),)
        self.assertIn("copy_totp", self._ids(schema=invented))

    def test_deleting_for_good_needs_ownership(self):
        self.assertNotIn(
            "delete_forever", self._ids(role=VaultRole.MEMBER, trashed=True)
        )
        self.assertIn("delete_forever", self._ids(role=VaultRole.OWNER, trashed=True))

    def test_a_stranger_is_offered_nothing(self):
        self.assertEqual(self._ids(role=None), [])
        self.assertEqual(self._ids(role=None, trashed=True), [])

    def test_favourite_and_unfavourite_are_both_offered(self):
        """Which of the two a menu shows is the client's business - it knows
        is_favorite from the entry it already holds. The registry answers
        what the *user* may do, not what the row currently is."""
        ids = self._ids()
        self.assertIn("favorite", ids)
        self.assertIn("unfavorite", ids)

    def test_every_action_id_is_unique(self):
        ids = [action.id for action in VaultActionRegistry.all()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_action_declares_a_known_category(self):
        for action in VaultActionRegistry.all():
            self.assertIn(
                action.category.value,
                {"edit", "organize", "clipboard", "danger"},
                action.id,
            )

    def test_every_registered_type_has_a_usable_schema(self):
        """schema_for is what the endpoint feeds the registry; a type whose
        schema it cannot produce would be an entry with no actions at all."""
        for entry_type in EntryType.values:
            self.assertTrue(schema_for(entry_type))
