"""Keeping secrets out of logs and error reports.

Distinct from :func:`workspace.common.logging.scrub`, which strips CR/LF to
stop log injection and hides nothing. This module hides values: a field whose
*name* marks it as a secret never reaches a log record, a traceback or an
error report, whatever code path put it there.

The catalogue matches names, not values, because the values are opaque
base64url by construction - there is nothing in a wrapped private key that
distinguishes it from a thumbnail path.
"""

import logging
import re

from django.views.debug import SafeExceptionReporterFilter

REDACTED = "[redacted]"

# password / secret_key / session_key anywhere in the name; wrapped_, encrypted_
# and sig_ only as a prefix, so `signature_count` and `designation` stay legible.
_SENSITIVE_NAME = re.compile(
    r"(password|secret_key|session_key|^wrapped_|^encrypted_|^sig_)", re.IGNORECASE
)

# name=value in a preformatted message. The value alternative puts the printf
# placeholders first so a lazily formatted record keeps its own arguments.
_ASSIGNMENT = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>%\([A-Za-z0-9_]+\)s|%[sdr]|[^\s,;]+)"
)


def is_sensitive_name(name) -> bool:
    """True if a field called *name* must never appear in a log or a report."""
    return bool(_SENSITIVE_NAME.search(str(name)))


def redact(value):
    """Redact *value*, walking into mappings and sequences by key name."""
    if isinstance(value, dict):
        return {
            key: REDACTED if is_sensitive_name(key) else redact(inner)
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    return value


class SecretRedactingFilter(logging.Filter):
    """Strip secrets from a record before any handler formats it.

    Two shapes are covered: a secret carried in a logging argument, and a
    secret already spelled out in the message as ``name=value``. Both come
    from the same well-meant line - "log the request body so we can debug
    this" - and neither is visible in review once it is there.
    """

    def filter(self, record):
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(arg) for arg in record.args)
        record.msg = self._redact_assignments(record.msg)
        return True

    @staticmethod
    def _redact_assignments(message):
        if not isinstance(message, str):
            return message

        def replace(match):
            name = match.group("name")
            value = REDACTED if is_sensitive_name(name) else match.group("value")
            return f"{name}={value}"

        return _ASSIGNMENT.sub(replace, message)


class RedactingExceptionReporterFilter(SafeExceptionReporterFilter):
    """Extend Django's own catalogue with this project's field names.

    A traceback is where a secret escapes even when no line ever logged it,
    because every frame's local variables are rendered. Django already hides
    ``password``-ish names; the wrapped keys, the ciphertexts and the
    signatures are ours to declare.
    """

    def is_active(self, request):
        # Django's own filter only activates outside DEBUG. A secret rendered
        # into a developer's browser is still a secret written to a log
        # somewhere upstream, so this one never stands down.
        return True

    def get_post_parameters(self, request):
        parameters = super().get_post_parameters(request)
        if not hasattr(parameters, "items"):
            return parameters
        return {
            key: REDACTED if is_sensitive_name(key) else value
            for key, value in parameters.items()
        }

    def cleanse_setting(self, key, value):
        if is_sensitive_name(key):
            return self.cleansed_substitute
        return super().cleanse_setting(key, value)
