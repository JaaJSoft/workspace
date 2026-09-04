"""The real action set.

What is pinned here is the part a client would otherwise guess: which actions
depend on the entry's type, which survive the trash, and which need
ownership. The registry's machinery is tested in test_actions.py.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from workspace.vault.actions import VaultActionRegistry
from workspace.vault.models import EntryType, VaultRole
from workspace.vault.types import Field, LoginEntry, schema_for


class _Entry:
    type = EntryType.LOGIN


class EntryActionTests(SimpleTestCase):
    def _ids(
        self, *, role=VaultRole.OWNER, trashed=False, schema=None, present_fields=None
    ):
        return [
            action["id"]
            for action in VaultActionRegistry.get_available_actions(
                None,
                _Entry(),
                role=role,
                trashed=trashed,
                schema=LoginEntry.FIELD_SCHEMA if schema is None else schema,
                present_fields=(
                    frozenset({"username", "password", "totp", "uri"})
                    if present_fields is None
                    else present_fields
                ),
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

    def test_a_field_action_needs_the_entry_to_carry_the_field(self):
        """Declaring a field and having one are different things. A login
        entry with no authenticator key must not be offered the action that
        copies a code it does not have - the menu would be lying, and the
        endpoint behind it would have nothing to act on."""
        carried = frozenset({"username", "password"})
        ids = self._ids(present_fields=carried)
        self.assertNotIn("copy_totp", ids)
        self.assertIn("copy_password", ids)

    def test_a_field_action_is_offered_once_the_entry_carries_the_field(self):
        self.assertIn("copy_totp", self._ids(present_fields=frozenset({"totp"})))

    def test_a_field_the_type_does_not_declare_is_never_offered(self):
        """Both gates hold: carrying a value cannot buy an action the type
        never declared, or a custom field could reach a reserved action."""
        without_totp = tuple(
            field for field in LoginEntry.FIELD_SCHEMA if field.field_id != "totp"
        )
        self.assertNotIn(
            "copy_totp",
            self._ids(schema=without_totp, present_fields=frozenset({"totp"})),
        )


class MenuHonoursTheRegistryTests(SimpleTestCase):
    """The two halves of a server-driven menu, held against each other.

    The registry answers what the caller may do; the browser decides what to
    render. An id offered by one and unknown to the other is a menu row that
    does nothing when clicked - the exact drift this design exists to prevent,
    and one nothing else would catch: the endpoint is right, the client is
    silent, and every test on either side passes.
    """

    # The ids the browser can carry out are declared in one array, which this
    # test reads rather than restates - a copy here would drift the same way.
    SOURCE = (
        Path(settings.BASE_DIR)
        / "workspace/vault/ui/static/vault/ui/js/vault_browser.js"
    )

    # Offered by the registry and not built yet. Adding an action to the
    # registry therefore fails this test until someone either implements it or
    # writes it down here.
    NOT_IMPLEMENTED_YET = {"move", "set_tags"}

    def _handled(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        block = re.search(
            r"window\.VAULT_HANDLED_ENTRY_ACTIONS = \[(.*?)\];", source, re.S
        )
        self.assertIsNotNone(block, "the browser must declare what it handles")
        return set(re.findall(r"'([a-z_]+)'", block.group(1)))

    def test_every_handled_id_is_an_action_the_registry_has(self):
        registered = {action.id for action in VaultActionRegistry.all()}
        self.assertEqual(self._handled() - registered, set())

    def test_every_registered_action_is_handled_or_written_down_as_pending(self):
        registered = {action.id for action in VaultActionRegistry.all()}
        self.assertEqual(registered - self._handled(), self.NOT_IMPLEMENTED_YET)
