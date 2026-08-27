"""The registry's machinery, exercised with throwaway actions.

The real action set lives in actions/entry.py and is tested separately: a
test that asserts machinery behaviour through a production action fails
whenever that action's rules change, which is a false alarm about the wrong
thing.
"""

from django.test import SimpleTestCase

from workspace.vault.actions import VaultActionRegistry
from workspace.vault.actions.base import ActionCategory, BaseVaultAction
from workspace.vault.models import VaultRole
from workspace.vault.types import LoginEntry


class _Entry:
    """Stand-in for a VaultEntry. The base class only reads what it is
    given, so no database is needed to prove that."""

    type = "login"


class RegistryMachineryTests(SimpleTestCase):
    def setUp(self):
        VaultActionRegistry._reset()
        self.addCleanup(VaultActionRegistry._reset)
        self.schema = LoginEntry.FIELD_SCHEMA

    def _register(self, **attrs):
        namespace = {
            "id": "sample",
            "label": "Sample",
            "icon": "circle",
            "category": ActionCategory.EDIT,
        }
        namespace.update(attrs)
        return VaultActionRegistry.register(
            type("SampleAction", (BaseVaultAction,), namespace)
        )

    def _available(self, *, role=VaultRole.OWNER, trashed=False):
        return VaultActionRegistry.get_available_actions(
            None, _Entry(), role=role, trashed=trashed, schema=self.schema
        )

    def test_a_registered_action_is_offered(self):
        self._register()
        self.assertEqual([action["id"] for action in self._available()], ["sample"])

    def test_no_role_means_no_action_at_all(self):
        self._register()
        self.assertEqual(self._available(role=None), [])

    def test_an_owner_only_action_is_hidden_from_a_member(self):
        self._register(min_role=VaultRole.OWNER)
        self.assertEqual(self._available(role=VaultRole.MEMBER), [])
        self.assertEqual(len(self._available(role=VaultRole.OWNER)), 1)

    def test_a_member_action_is_offered_to_an_owner(self):
        """Ownership is the stronger role, not a different one: an action
        open to members must not disappear for the owner."""
        self._register(min_role=VaultRole.MEMBER)
        self.assertEqual(len(self._available(role=VaultRole.OWNER)), 1)

    def test_the_trash_hides_an_action_that_did_not_opt_in(self):
        self._register()
        self.assertEqual(self._available(trashed=True), [])

    def test_an_action_that_opted_in_survives_the_trash(self):
        self._register(available_when_trashed=True)
        self.assertEqual(len(self._available(trashed=True)), 1)

    def test_an_action_that_opted_in_is_hidden_outside_the_trash(self):
        """Restoring an entry that is not in the trash is meaningless, so
        the opt-in is a swap, not a widening."""
        self._register(available_when_trashed=True, only_when_trashed=True)
        self.assertEqual(self._available(trashed=False), [])

    def test_serialisation_carries_exactly_what_a_menu_needs(self):
        self._register(css_class="text-error", supports_bulk=True)
        self.assertEqual(
            set(self._available()[0]),
            {"id", "label", "icon", "category", "css_class", "bulk"},
        )

    def test_the_category_serialises_as_its_value(self):
        self._register(category=ActionCategory.DANGER)
        self.assertEqual(self._available()[0]["category"], "danger")

    def test_an_override_that_forgets_super_is_the_caller_s_problem(self):
        """Documented contract: an override narrows, never widens. This test
        exists so the base class's checks are known to run first - remove the
        super() call in BaseVaultAction.is_available and it fails."""

        class Narrower(BaseVaultAction):
            id = "narrow"
            label = "Narrow"
            icon = "circle"
            category = ActionCategory.EDIT

            def is_available(self, user, entry, **state):
                return super().is_available(user, entry, **state)

        VaultActionRegistry.register(Narrower)
        self.assertEqual(self._available(role=None), [])
