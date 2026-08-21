import logging

from django.conf import settings
from django.test import RequestFactory, TestCase
from django.views.debug import get_exception_reporter_filter

from workspace.common.redaction import (
    REDACTED,
    RedactingExceptionReporterFilter,
    SecretRedactingFilter,
    is_sensitive_name,
)


class SensitiveNameTests(TestCase):
    def test_matches_the_catalogue(self):
        for name in (
            "password",
            "vault_password",
            "secret_key",
            "session_key",
            "wrapped_kex_priv",
            "encrypted_name",
            "sig_over_kex_pub",
            "SIG_PUBLIC",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_sensitive_name(name))

    def test_leaves_ordinary_names_alone(self):
        for name in (
            "username",
            "kdf_params",
            "kdf_salt",
            "state",
            "signature_count",
            "design",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_sensitive_name(name))


class SecretRedactingFilterTests(TestCase):
    def _render(self, msg, *args):
        record = logging.LogRecord(
            "workspace.test", logging.INFO, __file__, 1, msg, args or None, None
        )
        SecretRedactingFilter().filter(record)
        return record.getMessage()

    def test_redacts_a_secret_spelled_out_in_a_message(self):
        rendered = self._render(
            "identity payload wrapped_kex_priv=AAAABBBBCCCCDDDD done"
        )
        self.assertNotIn("AAAABBBBCCCCDDDD", rendered)
        self.assertIn(REDACTED, rendered)

    def test_redacts_a_secret_inside_a_dict_argument(self):
        rendered = self._render(
            "body=%(wrapped_sig_priv)s state=%(state)s",
            {"wrapped_sig_priv": "SECRETVALUE", "state": "pending"},
        )
        self.assertNotIn("SECRETVALUE", rendered)
        self.assertIn("pending", rendered)

    def test_redacts_a_secret_nested_in_a_positional_argument(self):
        rendered = self._render("body=%s", {"wrapped_sig_priv": "SECRETVALUE"})
        self.assertNotIn("SECRETVALUE", rendered)

    def test_a_positional_secret_survives_formatting(self):
        """Dropping a placeholder from the format string while its argument
        stays in record.args makes getMessage() raise, and logging's own error
        handler then prints Message and Arguments to stderr - the filter would
        publish the secret it exists to hide."""
        record = logging.LogRecord(
            "workspace.test",
            logging.INFO,
            __file__,
            1,
            "wrapped_kex_priv=%s",
            ("SECRETVALUE",),
            None,
        )
        SecretRedactingFilter().filter(record)
        rendered = record.getMessage()
        self.assertNotIn("SECRETVALUE", rendered)

    def test_a_sensitive_placeholder_redacts_its_neighbours_too(self):
        """Positional arguments cannot be matched to names, so a format string
        that names a secret has all of them redacted rather than guessing."""
        record = logging.LogRecord(
            "workspace.test",
            logging.INFO,
            __file__,
            1,
            "user=%s wrapped_sig_priv=%s",
            ("alice", "SECRETVALUE"),
            None,
        )
        SecretRedactingFilter().filter(record)
        self.assertNotIn("SECRETVALUE", record.getMessage())

    def test_leaves_an_ordinary_message_untouched(self):
        self.assertEqual(self._render("synced %s folders", 3), "synced 3 folders")

    def test_leaves_an_ordinary_assignment_untouched(self):
        self.assertEqual(self._render("state=pending"), "state=pending")

    def test_survives_a_non_string_message(self):
        self.assertEqual(self._render(42), "42")


class ExceptionReporterFilterTests(TestCase):
    def test_redacts_a_sensitive_post_parameter(self):
        request = RequestFactory().post(
            "/api/v1/vault/account/finalize",
            data={"wrapped_kex_priv": "SECRETVALUE", "kdf_algo": "argon2id"},
        )
        cleansed = RedactingExceptionReporterFilter().get_post_parameters(request)
        self.assertEqual(cleansed["wrapped_kex_priv"], REDACTED)
        self.assertEqual(cleansed["kdf_algo"], "argon2id")

    def test_redacts_a_sensitive_setting(self):
        reporter = RedactingExceptionReporterFilter()
        self.assertEqual(
            reporter.cleanse_setting("WRAPPED_KEY_STORE", "SECRETVALUE"),
            reporter.cleansed_substitute,
        )
        self.assertEqual(
            reporter.cleanse_setting("PREVIEW_VISIBILITY", "staff"), "staff"
        )


class WiringTests(TestCase):
    def test_the_console_handler_carries_the_filter(self):
        self.assertIn(
            "redact_secrets", settings.LOGGING["handlers"]["console"]["filters"]
        )

    def test_the_project_reporter_filter_is_ours(self):
        request = RequestFactory().get("/")
        self.assertIsInstance(
            get_exception_reporter_filter(request), RedactingExceptionReporterFilter
        )
