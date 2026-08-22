import logging
from collections import namedtuple
from types import MappingProxyType

from django.conf import settings
from django.test import RequestFactory, TestCase
from django.views.debug import get_exception_reporter_filter

from workspace.common.redaction import (
    REDACTED,
    RedactingExceptionReporterFilter,
    SecretRedactingFilter,
    is_sensitive_name,
)

_Point = namedtuple("_Point", "x y")


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

    def test_matches_the_names_other_modules_hold_secrets_under(self):
        for name in (
            "access_token",
            "refresh_token",
            "share_token",
            "oauth2_data_encrypted",
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
            # Counters, not credentials - the suffix rule is singular for them.
            "prompt_tokens",
            "completion_tokens",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_sensitive_name(name))


class SecretRedactingFilterTests(TestCase):
    def _render_through_logger(self, msg, args):
        """The production path, which a hand-built LogRecord does not reproduce.

        logging turns a lone Mapping argument into ``record.args`` itself, and
        passing one straight to ``LogRecord(...)`` raises instead. A test
        written on that shortcut proves nothing about what ships.
        """
        rendered = []

        class _Sink(logging.Handler):
            def emit(self, record):
                rendered.append(record.getMessage())

        logger = logging.getLogger("workspace.test.redaction")
        logger.handlers.clear()
        logger.filters.clear()
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        handler = _Sink()
        handler.addFilter(SecretRedactingFilter())
        logger.addHandler(handler)
        self.addCleanup(logger.handlers.clear)

        logger.warning(msg, args)
        return rendered[0]

    def test_redacts_a_secret_in_a_mapping_that_is_not_a_dict(self):
        """logging accepts any collections.abc.Mapping as named arguments, so
        a filter that only knows dict lets the rest through untouched - and
        silently, which is the worst way for a redaction filter to fail."""
        rendered = self._render_through_logger(
            "wrapped_sig_priv=%(wrapped_sig_priv)s",
            MappingProxyType({"wrapped_sig_priv": "SECRETVALUE"}),
        )
        self.assertNotIn("SECRETVALUE", rendered)
        self.assertIn(REDACTED, rendered)

    def test_redacts_a_secret_in_a_plain_dict_through_the_same_path(self):
        rendered = self._render_through_logger(
            "wrapped_sig_priv=%(wrapped_sig_priv)s",
            {"wrapped_sig_priv": "SECRETVALUE"},
        )
        self.assertNotIn("SECRETVALUE", rendered)

    def test_redacts_a_mapping_nested_inside_a_dict(self):
        rendered = self._render_through_logger(
            "body=%(body)s",
            {"body": MappingProxyType({"wrapped_kex_priv": "SECRETVALUE"})},
        )
        self.assertNotIn("SECRETVALUE", rendered)

    def test_a_namedtuple_argument_leaves_the_logging_call_alive(self):
        """A tuple subclass cannot be rebuilt from an iterable - a namedtuple
        takes one positional argument per field. Handlers call filters outside
        the try/except around emit, so a filter that raises here takes down the
        logger.warning() call itself, anywhere in the project."""
        rendered = self._render_through_logger("point %s", _Point(1, 2))
        self.assertIn("_Point(x=1, y=2)", rendered)

    def test_redacts_a_secret_nested_in_a_namedtuple(self):
        rendered = self._render_through_logger(
            "point %s", _Point({"password": "SECRETVALUE"}, 2)
        )
        self.assertNotIn("SECRETVALUE", rendered)
        self.assertIn(REDACTED, rendered)

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

    def test_a_secret_beside_a_numeric_conversion_still_renders(self):
        """A redacted string handed to a %d makes getMessage() raise, and
        logging drops the whole record - the line the call existed to write is
        simply lost, silently."""
        record = logging.LogRecord(
            "workspace.test",
            logging.INFO,
            __file__,
            1,
            "attempts=%d password=%s",
            (3, "SECRETVALUE"),
            None,
        )
        SecretRedactingFilter().filter(record)
        rendered = record.getMessage()
        self.assertNotIn("SECRETVALUE", rendered)
        self.assertIn("attempts=", rendered)

    def test_leaves_an_ordinary_message_untouched(self):
        self.assertEqual(self._render("synced %s folders", 3), "synced 3 folders")

    def test_leaves_an_ordinary_assignment_untouched(self):
        self.assertEqual(self._render("state=pending"), "state=pending")

    def test_survives_a_non_string_message_carrying_arguments(self):
        """A record can be built with a non-string message and arguments. The
        scan for a secret named in the format string has to cope with having
        no format string; whether such a record renders at all is logging's
        own problem, so the filter passes it through untouched."""
        record = logging.LogRecord(
            "workspace.test", logging.INFO, __file__, 1, 42, ("x",), None
        )
        self.assertTrue(SecretRedactingFilter().filter(record))
        self.assertEqual(record.msg, 42)
        self.assertEqual(record.args, ("x",))

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
