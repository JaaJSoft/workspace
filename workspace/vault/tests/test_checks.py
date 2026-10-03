from unittest import mock

from django.core.checks import Tags, registry
from django.db import OperationalError, connection
from django.test import SimpleTestCase, TestCase, override_settings

from workspace.vault.checks import retired_ids_check, test_switches_check
from workspace.vault.models import AccountIdentity
from workspace.vault.services import suites
from workspace.vault.tests.factories import make_account, make_vault, sealed


class TestSwitchCheckTests(SimpleTestCase):
    @override_settings(VAULT_TEST_SUITES=True, VAULT_TEST_RUNNER_ACTIVE=False)
    def test_test_suites_outside_the_runner_is_an_error(self):
        errors = test_switches_check(None)
        self.assertEqual([error.id for error in errors], ["vault.E002"])

    @override_settings(
        VAULT_TEST_SUITES=False,
        VAULT_TEST_MANIFEST={"aead": {}},
        VAULT_TEST_RUNNER_ACTIVE=False,
    )
    def test_a_test_manifest_outside_the_runner_is_an_error(self):
        self.assertEqual([e.id for e in test_switches_check(None)], ["vault.E002"])

    @override_settings(VAULT_TEST_SUITES=True, VAULT_TEST_RUNNER_ACTIVE=True)
    def test_the_runner_may_turn_them_on(self):
        self.assertEqual(test_switches_check(None), [])

    @override_settings(
        VAULT_TEST_SUITES=False,
        VAULT_TEST_MANIFEST=None,
        VAULT_TEST_RUNNER_ACTIVE=False,
    )
    def test_production_defaults_pass(self):
        self.assertEqual(test_switches_check(None), [])


class RetiredIdsCheckTests(TestCase):
    databases = {"default"}

    def test_registered_as_a_database_check(self):
        self.assertIn(
            retired_ids_check,
            registry.registry.get_checks(include_deployment_checks=False),
        )
        self.assertIn(Tags.database, getattr(retired_ids_check, "tags", ()))

    def test_passes_when_every_stored_id_is_declared(self):
        user, _, _ = make_account("owner")
        make_vault(user, encrypted_name=sealed("v", 1))
        self.assertEqual(retired_ids_check(None, databases=["default"]), [])

    def test_fails_when_rows_use_an_id_the_manifest_dropped(self):
        user, _, _ = make_account("owner")
        make_vault(user, encrypted_name=sealed("v", 1))
        real_state = suites.state

        def without_format_1(axis, identifier):
            if (axis, identifier) == ("format", 1):
                return None
            return real_state(axis, identifier)

        with mock.patch(
            "workspace.vault.checks.suites.state", side_effect=without_format_1
        ):
            errors = retired_ids_check(None, databases=["default"])
        self.assertEqual([error.id for error in errors], ["vault.E001"])
        self.assertIn("format 1", errors[0].msg)

    def test_skips_without_databases(self):
        self.assertEqual(retired_ids_check(None), [])

    def test_passes_on_a_database_without_vault_tables(self):
        with (
            mock.patch.object(connection.introspection, "table_names", return_value=[]),
            mock.patch(
                "workspace.vault.checks.census_service.census",
                side_effect=AssertionError(
                    "census ran on a database without vault tables"
                ),
            ),
        ):
            self.assertEqual(retired_ids_check(None, databases=["default"]), [])

    def test_an_unreadable_head_is_not_a_retired_id(self):
        user, _, _ = make_account("owner")
        make_vault(user, encrypted_name="!!!!")
        self.assertEqual(retired_ids_check(None, databases=["default"]), [])

    def test_a_control_character_in_an_id_never_reaches_the_message_raw(self):
        user, _, identity = make_account("owner")
        identity.kdf_algo = "bad\nalgo"
        identity.save(update_fields=["kdf_algo"])
        errors = retired_ids_check(None, databases=["default"])
        self.assertEqual([error.id for error in errors], ["vault.E001"])
        self.assertNotIn("\n", errors[0].msg)

    def test_a_database_error_while_counting_is_not_an_error(self):
        with mock.patch(
            "workspace.vault.checks.census_service.census",
            side_effect=OperationalError("no such column"),
        ):
            self.assertEqual(retired_ids_check(None, databases=["default"]), [])


class RetirementAfterRecordMigrationTests(TestCase):
    """What a record migration leaves behind: every vault row on format 2,
    the account envelope still on format 1, because it is not that
    migration's to move. Format 1 still cannot be dropped from the manifest."""

    databases = {"default"}

    def test_format_1_still_cannot_be_retired_while_the_envelope_uses_it(self):
        user, _, identity = make_account("owner")
        AccountIdentity.objects.filter(pk=identity.pk).update(
            wrapped_kex_priv=sealed("k", 1)
        )
        make_vault(user)  # every record already format 2
        real_state = suites.state

        def without_format_1(axis, identifier):
            if (axis, identifier) == ("format", 1):
                return None
            return real_state(axis, identifier)

        with mock.patch(
            "workspace.vault.checks.suites.state", side_effect=without_format_1
        ):
            errors = retired_ids_check(None, databases=["default"])
        self.assertEqual([error.id for error in errors], ["vault.E001"])
