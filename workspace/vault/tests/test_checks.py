from django.test import SimpleTestCase, override_settings

from workspace.vault.checks import test_switches_check


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
