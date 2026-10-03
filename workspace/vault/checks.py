"""System checks the vault registers in VaultConfig.ready()."""

from django.conf import settings
from django.core.checks import Error


def test_switches_check(app_configs, **kwargs):
    """The test algorithms and the manifest overrides exist to prove the
    dispatch. Switched on in production, the test AEAD could become the one
    every migration writes, and switching it off again would leave those rows
    unreadable."""
    switched_on = settings.VAULT_TEST_SUITES or settings.VAULT_TEST_MANIFEST
    if switched_on and not settings.VAULT_TEST_RUNNER_ACTIVE:
        return [
            Error(
                "VAULT_TEST_SUITES or VAULT_TEST_MANIFEST is set outside the test runner.",
                hint="Unset them: they are only for the vault's own test suite.",
                id="vault.E002",
            )
        ]
    return []
