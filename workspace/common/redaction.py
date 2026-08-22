"""Keeping secrets out of logs and error reports.

Distinct from :func:`workspace.common.logging.scrub`, which strips CR/LF to
stop log injection and hides nothing. This module hides values: a field whose
*name* marks it as a secret never reaches a log record, a traceback or an
error report, whatever code path put it there.

The catalogue matches names, not values, because the values are opaque
base64url by construction - there is nothing in a wrapped private key that
distinguishes it from a thumbnail path.

Scope: every logger that reaches the project's console handler, plus anything
an error reporter renders. Access logs are not covered - `django.server` keeps
Django's own handler and does not propagate, and in production the access log
belongs to gunicorn, outside Django's logging entirely. Nothing here puts a
secret in a URL, so no value crosses that line today; a feature that did would
need the handler taught about this filter as well.
"""

import logging
import re
from collections.abc import Mapping

from django.views.debug import SafeExceptionReporterFilter

REDACTED = "[redacted]"

# password / secret_key / session_key anywhere in the name; wrapped_, encrypted_
# and sig_ only as a prefix, so `signature_count` and `designation` stay legible.
_SENSITIVE_NAME = re.compile(
    r"(password|secret_key|session_key|^wrapped_|^encrypted_|^sig_)", re.IGNORECASE
)

# name=value in a message, whether already formatted or still a format string.
_ASSIGNMENT = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s,;]+)")

# A printf conversion, so an assignment whose value is still a placeholder can
# be told apart from one carrying a literal.
_CONVERSION = re.compile(r"%(?:\([A-Za-z0-9_]+\))?[-+ #0-9.]*[a-zA-Z%]")


def is_sensitive_name(name) -> bool:
    """True if a field called *name* must never appear in a log or a report."""
    return bool(_SENSITIVE_NAME.search(str(name)))


def redact(value):
    """Redact *value*, walking into mappings and sequences by key name.

    Every Mapping, not only dict: logging accepts any of them as a record's
    named arguments, and a read-only or custom one is still a mapping whose
    keys name secrets. A plain dict comes back - the redacted copy has no
    reason to keep the original's type, and some cannot be rebuilt anyway.

    A sequence holding nothing sensitive comes back as the very object that
    went in: a tuple subclass takes one positional argument per field, so
    rebuilding it from an iterable raises - and a filter that raises inside a
    handler kills the logging call itself.
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_name(key) else redact(inner)
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        rebuilt = [redact(item) for item in value]
        if all(new is old for new, old in zip(rebuilt, value, strict=True)):
            return value
        return rebuilt if isinstance(value, list) else tuple(rebuilt)
    return value


class SecretRedactingFilter(logging.Filter):
    """Strip secrets from a record before any handler formats it.

    Two shapes are covered: a secret carried in a logging argument, and a
    secret already spelled out in the message as ``name=value``. Both come
    from the same well-meant line - "log the request body so we can debug
    this" - and neither is visible in review once it is there.

    A secret passed positionally cannot be matched to the name in front of its
    placeholder, so a format string that names one has every positional
    argument redacted. Blunt, and the only direction that fails closed.
    """

    def filter(self, record):
        # Both passes below are full regex scans of the message, and an
        # assignment is what they look for - one substring test spares that
        # cost to the records that hold none, which is nearly all of them.
        scannable = isinstance(record.msg, str) and "=" in record.msg
        if isinstance(record.args, Mapping):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            if scannable and self._names_a_secret_positionally(record.msg):
                record.args = tuple(REDACTED for _ in record.args)
            else:
                record.args = tuple(redact(arg) for arg in record.args)
        if scannable:
            record.msg = self._redact_assignments(record.msg)
        return True

    @staticmethod
    def _names_a_secret_positionally(message):
        return any(
            is_sensitive_name(match.group("name"))
            and _CONVERSION.fullmatch(match.group("value"))
            for match in _ASSIGNMENT.finditer(message)
        )

    @staticmethod
    def _redact_assignments(message):
        def replace(match):
            value = match.group("value")
            # A placeholder is left in place. Removing it while its argument
            # stays in record.args makes getMessage() raise, and logging's
            # error handler prints the arguments to stderr itself.
            if not is_sensitive_name(match.group("name")) or _CONVERSION.fullmatch(
                value
            ):
                return match.group(0)
            return f"{match.group('name')}={REDACTED}"

        return _ASSIGNMENT.sub(replace, message)


class RedactingExceptionReporterFilter(SafeExceptionReporterFilter):
    """Extend Django's own catalogue with this project's field names.

    A traceback is where a secret escapes even when no line ever logged it,
    because every frame's local variables are rendered. Django already hides
    ``password``-ish names; the wrapped keys, the ciphertexts and the
    signatures are ours to declare.
    """

    def is_active(self, request):
        # Django's own filter stands down under DEBUG, on the reasoning that a
        # developer wants the whole frame. A DEBUG deployment is precisely the
        # accident where the technical 500 page reaches someone it should not,
        # so this one never stands down.
        return True

    def get_traceback_frame_variables(self, request, tb_frame):
        # Django cleanses a frame's locals only where @sensitive_variables
        # named them, which leaves every undecorated frame rendering whatever
        # it happens to hold. Matching on the name catches the rest; a view
        # holding key material under an innocent name still has to declare it.
        return [
            (name, self.cleansed_substitute if is_sensitive_name(name) else value)
            for name, value in super().get_traceback_frame_variables(request, tb_frame)
        ]

    def get_post_parameters(self, request):
        parameters = super().get_post_parameters(request)
        return {
            key: REDACTED if is_sensitive_name(key) else value
            for key, value in parameters.items()
        }

    def cleanse_setting(self, key, value):
        if is_sensitive_name(key):
            return self.cleansed_substitute
        return super().cleanse_setting(key, value)
