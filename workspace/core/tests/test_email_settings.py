"""The email settings: off by default, and their secrets kept out of reports."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

import workspace.settings
from workspace.common.redaction import (
    RedactingExceptionReporterFilter,
    is_sensitive_name,
)

_MODULE = Path(workspace.settings.__file__).with_name("email.py")


def _load(env):
    """A fresh copy of the settings module read under ``env`` alone, as far
    as EMAIL_* variables go."""
    environ = {
        k: v for k, v in os.environ.items() if not k.startswith(("EMAIL_", "ANYMAIL_"))
    }
    environ.update(env)
    spec = importlib.util.spec_from_file_location(
        "workspace.settings._email_probe", _MODULE
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, environ, clear=True):
        spec.loader.exec_module(module)
    return module


class EmailSettingsTests(SimpleTestCase):
    def test_nothing_configured_sends_nothing(self):
        module = _load({})
        self.assertFalse(module.EMAIL_ENABLED)
        self.assertEqual(
            module.EMAIL_BACKEND, "django.core.mail.backends.dummy.EmailBackend"
        )

    def test_a_host_turns_sending_on_over_smtp(self):
        module = _load({"EMAIL_HOST": "smtp.example.com"})
        self.assertTrue(module.EMAIL_ENABLED)
        self.assertEqual(
            module.EMAIL_BACKEND, "django.core.mail.backends.smtp.EmailBackend"
        )
        self.assertEqual(module.EMAIL_PORT, 587)
        self.assertTrue(module.EMAIL_USE_TLS)

    def test_a_host_can_be_muted(self):
        module = _load({"EMAIL_HOST": "smtp.example.com", "EMAIL_ENABLED": "false"})
        self.assertFalse(module.EMAIL_ENABLED)

    def test_implicit_tls_moves_to_465(self):
        module = _load({"EMAIL_HOST": "smtp.example.com", "EMAIL_USE_SSL": "true"})
        self.assertEqual(module.EMAIL_PORT, 465)
        self.assertFalse(module.EMAIL_USE_TLS)

    def test_refuses_both_tls_modes(self):
        with self.assertRaises(ImproperlyConfigured):
            _load({"EMAIL_USE_SSL": "true", "EMAIL_USE_TLS": "true"})

    def test_anymail_is_configured_from_the_environment(self):
        module = _load(
            {
                "ANYMAIL_WEBHOOK_SECRET": "relay:s3cret",
                "ANYMAIL_POSTMARK_SERVER_TOKEN": "pm-token",
                "ANYMAIL_EMPTY": "",
            }
        )
        self.assertEqual(module.ANYMAIL["WEBHOOK_SECRET"], "relay:s3cret")
        self.assertEqual(module.ANYMAIL["POSTMARK_SERVER_TOKEN"], "pm-token")
        self.assertNotIn("EMPTY", module.ANYMAIL)

    def test_server_email_defaults_to_the_from_address(self):
        module = _load({"DEFAULT_FROM_EMAIL": "Acme <noreply@acme.test>"})
        self.assertEqual(module.SERVER_EMAIL, "Acme <noreply@acme.test>")


class EmailSecretsTests(SimpleTestCase):
    SECRETS = ("EMAIL_HOST_PASSWORD", "WEBHOOK_SECRET", "POSTMARK_SERVER_TOKEN")

    def test_secret_settings_are_in_the_redaction_catalogue(self):
        for name in self.SECRETS:
            with self.subTest(name=name):
                self.assertTrue(is_sensitive_name(name))

    def test_secret_settings_never_reach_the_error_page(self):
        reporter = RedactingExceptionReporterFilter()
        self.assertEqual(
            reporter.cleanse_setting("EMAIL_HOST_PASSWORD", "hunter2"),
            reporter.cleansed_substitute,
        )
        cleansed = reporter.cleanse_setting(
            "ANYMAIL",
            {"WEBHOOK_SECRET": "relay:hunter2", "MAILGUN_API_KEY": "key-hunter2"},
        )
        self.assertNotIn("hunter2", str(cleansed))
