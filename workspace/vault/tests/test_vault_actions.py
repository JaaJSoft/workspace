"""The actions a vault carries, as opposed to those an entry carries.

Every one of them writes a field the vault's signed metadata covers, and
that payload names the *owner* - so unlike an entry, where a member signs
what they change, none of these is offered below ownership. What is pinned
here is that floor, because it is the difference between sharing being an
addition and being a hole.
"""

from django.test import SimpleTestCase

from workspace.vault.actions.vault import VaultTargetActionRegistry
from workspace.vault.models import VaultRole


class _Vault:
    """Stand-in for a Vault row. The actions read the role they are given
    and nothing off the object, so no database is needed to prove it."""

    is_favorite = False


class VaultActionTests(SimpleTestCase):
    def _ids(self, *, role=VaultRole.OWNER):
        return [
            action["id"]
            for action in VaultTargetActionRegistry.get_available_actions(
                None, _Vault(), role=role
            )
        ]

    def test_the_owner_is_offered_every_vault_action(self):
        self.assertEqual(
            self._ids(),
            ["rename", "set_appearance", "favorite", "unfavorite", "delete"],
        )

    def test_a_member_is_offered_none_of_them(self):
        """Not a narrowing but an emptying: every vault action rewrites the
        signed metadata, whose payload names the owner. A member holding a
        key wrap can open the vault and cannot re-sign what describes it."""
        self.assertEqual(self._ids(role=VaultRole.MEMBER), [])

    def test_a_caller_with_no_role_is_offered_nothing(self):
        self.assertEqual(self._ids(role=None), [])

    def test_opening_is_not_an_action(self):
        """Navigation is not a permission. A vault the caller can see can be
        opened, so an "open" action would answer yes every time it was asked
        and give a client the illusion of a gate."""
        self.assertNotIn("open", self._ids())

    def test_the_entry_registry_is_untouched_by_this_one(self):
        """The two registries share a base class, and __init_subclass__ is
        what keeps their lists apart. Were it removed, the vault actions
        would register into the entry registry and be offered on entries."""
        from workspace.vault.actions import VaultActionRegistry

        self.assertNotIn(
            "set_appearance", [action.id for action in VaultActionRegistry.all()]
        )
        self.assertNotIn(
            "copy_password",
            [action.id for action in VaultTargetActionRegistry.all()],
        )

    def test_serialisation_carries_exactly_what_a_menu_needs(self):
        action = VaultTargetActionRegistry.get_available_actions(
            None, _Vault(), role=VaultRole.OWNER
        )[0]
        self.assertEqual(
            set(action), {"id", "label", "icon", "category", "css_class", "bulk"}
        )
