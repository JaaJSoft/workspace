"""System checks the vault registers in VaultConfig.ready()."""

from django.conf import settings
from django.core.checks import Error
from django.db import DatabaseError, connections

from .models import (
    AccountIdentity,
    EntryField,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from .services import census as census_service
from .services import suites

# Every table the census reads.
_VAULT_TABLES = {
    model._meta.db_table
    for model in (
        Vault,
        VaultKeyWrap,
        VaultFolder,
        VaultTag,
        VaultEntry,
        EntryField,
        AccountIdentity,
    )
}


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


def retired_ids_check(app_configs, databases=None, **kwargs):
    """Refuse to run once the manifest drops an id stored rows still carry.

    manage.py migrate runs database-tagged checks before applying anything,
    so on a fresh install the tables do not exist yet: that is not an error,
    there is simply nothing to count.
    """
    if not databases:
        return []
    errors = []
    for alias in databases:
        connection = connections[alias]
        if not _VAULT_TABLES <= set(connection.introspection.table_names()):
            continue
        try:
            counts = census_service.census(using=alias)
        except DatabaseError:
            continue
        for (axis, identifier), count in sorted(counts.items(), key=str):
            if identifier == census_service.UNREADABLE_ID:
                continue
            if suites.state(axis, identifier) is None:
                shown = repr(identifier) if isinstance(identifier, str) else identifier
                errors.append(
                    Error(
                        f"{count} stored row(s) carry {axis} {shown}, which the "
                        "crypto suite manifest no longer declares.",
                        hint="Run `manage.py vault_suite_census` and restore the id "
                        "until it counts zero.",
                        id="vault.E001",
                    )
                )
    return errors
