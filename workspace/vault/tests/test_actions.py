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
        # A registry of this test's own, rather than the global one emptied
        # and refilled: the global is process-wide, its actions register at
        # import time, and a second import puts nothing back. Subclassing is
        # enough - __init_subclass__ hands out the empty list.
        self.registry = type("_TestRegistry", (VaultActionRegistry,), {})
        self.schema = LoginEntry.FIELD_SCHEMA

    def _register(self, **attrs):
        namespace = {
            "id": "sample",
            "label": "Sample",
            "icon": "circle",
            "category": ActionCategory.EDIT,
        }
        namespace.update(attrs)
        return self.registry.register(
            type("SampleAction", (BaseVaultAction,), namespace)
        )

    def _available(self, *, role=VaultRole.OWNER, trashed=False):
        return self.registry.get_available_actions(
            None, _Entry(), role=role, trashed=trashed, schema=self.schema
        )

    def test_a_subclass_registers_into_its_own_list(self):
        """The isolation this whole test class stands on. Were _actions
        inherited rather than handed out fresh, every registration here would
        land in the production registry and stay there for the rest of the
        process."""
        before = len(VaultActionRegistry.all())
        self._register()
        self.assertEqual(len(VaultActionRegistry.all()), before)
        self.assertNotIn("sample", [action.id for action in VaultActionRegistry.all()])

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

    def test_only_when_trashed_alone_is_offered_in_the_trash(self):
        """The natural single-flag reading. Requiring both flags makes an
        action that sets only this one offered nowhere at all, and nothing
        would reject the combination."""
        self._register(only_when_trashed=True)
        self.assertEqual(len(self._available(trashed=True)), 1)
        self.assertEqual(self._available(trashed=False), [])

    def test_a_role_the_rank_table_does_not_know_is_offered_nothing(self):
        """The floor is a rank comparison, not an equality against OWNER.
        A role added to the model but not to the table has to fall below
        every action rather than clear the ones asking for MEMBER."""
        self._register(min_role=VaultRole.MEMBER)
        self.assertEqual(self._available(role="viewer"), [])

    def test_is_action_available_answers_for_one_id(self):
        """What the restore and purge endpoints ask instead of restating an
        action's rules."""
        self._register(id="sample", min_role=VaultRole.OWNER)

        def ask(role):
            return self.registry.is_action_available(
                "sample", None, _Entry(), role=role, trashed=False, schema=self.schema
            )

        self.assertTrue(ask(VaultRole.OWNER))
        self.assertFalse(ask(VaultRole.MEMBER))

    def test_is_action_available_is_false_for_an_id_nobody_registered(self):
        self.assertFalse(
            self.registry.is_action_available(
                "ghost",
                None,
                _Entry(),
                role=VaultRole.OWNER,
                trashed=False,
                schema=self.schema,
            )
        )

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

        self.registry.register(Narrower)
        self.assertEqual(self._available(role=None), [])
