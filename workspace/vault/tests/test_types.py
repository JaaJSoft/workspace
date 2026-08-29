from django.test import SimpleTestCase, TestCase

from workspace.vault.models import EntryType, VaultEntry
from workspace.vault.tests.factories import make_account, make_vault
from workspace.vault.types import (
    Field,
    LoginEntry,
    as_typed,
    registry_for,
    schema_for,
    type_catalogue,
)


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
        # Built through type() rather than a class statement: defining the
        # class *is* the thing under test, so the name it would bind is dead.
        with self.assertRaises(ValueError):
            type(
                "BadEntry",
                (LoginEntry,),
                {
                    "__module__": __name__,
                    "Meta": type("Meta", (), {"proxy": True}),
                    "FIELD_SCHEMA": (Field("pin", label="PIN"),),
                },
            )

    def test_registry_for_an_unknown_type_raises(self):
        with self.assertRaises(KeyError):
            registry_for("passport")


class CatalogueTests(SimpleTestCase):
    """What the browser's New menu and entry form are built from.

    The catalogue is the registry rendered for the page: adding a type has to
    stay one Python class, so nothing on the JavaScript side may hold a list
    of types or of the fields one carries.
    """

    def test_the_catalogue_covers_every_registered_type(self):
        self.assertEqual(
            {entry["id"] for entry in type_catalogue()}, set(EntryType.values)
        )

    def test_a_type_carries_what_a_menu_needs_to_render_it(self):
        login = next(entry for entry in type_catalogue() if entry["id"] == "login")
        self.assertEqual(login["label"], "Login")
        self.assertTrue(login["icon"])

    def test_a_field_carries_what_a_form_needs_to_render_it(self):
        login = next(entry for entry in type_catalogue() if entry["id"] == "login")
        password = next(
            field for field in login["fields"] if field["field_id"] == "password"
        )
        self.assertEqual(
            password,
            {
                "field_id": "password",
                "label": "Password",
                "secret": True,
                "generator": True,
                "kind": "text",
            },
        )

    def test_the_catalogue_holds_nothing_but_json(self):
        """It is rendered by |json_script, which serializes with Django's own
        encoder: a dataclass or a proxy class reaching it is a 500."""
        import json

        json.dumps(type_catalogue())


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

    def test_the_manager_refuses_to_create_an_entry_of_another_type(self):
        """setdefault alone would persist the caller's type, and as_typed
        could then not re-cast the row the manager had just written."""
        user, _, _ = make_account("owner")
        vault = make_vault(user)
        with self.assertRaises(ValueError):
            LoginEntry.objects.create(
                vault=vault,
                type="passport",
                encrypted_name="AQ",
                metadata_sig="AQ",
            )
        self.assertFalse(VaultEntry.objects.exists())

    def test_the_manager_stamps_its_own_type_when_none_is_given(self):
        user, _, _ = make_account("owner")
        vault = make_vault(user)
        entry = LoginEntry.objects.create(
            vault=vault, encrypted_name="AQ", metadata_sig="AQ"
        )
        self.assertEqual(entry.type, EntryType.LOGIN)
