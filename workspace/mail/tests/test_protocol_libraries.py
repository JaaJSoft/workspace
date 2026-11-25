"""Mail and calendar leave IMAP and the message format to maintained libraries.

IMAP goes through IMAPClient, which parses the server's answers and encodes
mailbox names, and messages through the modern half of the standard
library's email package (`email.policy.default`: `EmailMessage`, the header
registry). The legacy half and raw imaplib are what the module was moved
off: every answer parsed with a regular expression and every header decoded
by hand came from there, and a new import of one is how that starts again.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

MAIL = Path(__file__).resolve().parents[1]
# Calendar sends iTIP replies over the mail account's SMTP: same rule.
SCANNED = (MAIL, MAIL.parent / "calendar")

# imaplib: the wire protocol, without a parser for its answers.
# email.mime, email.header: the legacy way to build a message and to decode
# its headers.
FORBIDDEN_MODULES = ("imaplib", "email.mime", "email.header")
FORBIDDEN_NAMES = {
    "email": {"message_from_string"},
}


def _sources():
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            if relative.parts[0] in ("tests", "migrations"):
                continue
            yield (
                f"{root.name}/{relative.as_posix()}",
                ast.parse(path.read_text(encoding="utf-8")),
            )


def _imports(tree):
    """``(module, name)`` for every import of `tree`; name is None for a
    plain ``import module``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                yield node.module, alias.name


class ProtocolLibraryTests(SimpleTestCase):
    def test_the_sources_are_found(self):
        names = [name for name, _tree in _sources()]

        self.assertIn("mail/services/imap_sync.py", names)
        self.assertIn("mail/services/smtp.py", names)
        self.assertIn("calendar/tasks.py", names)

    def test_no_raw_imap_and_no_legacy_email_api(self):
        offences = []
        for source, tree in _sources():
            for module, name in _imports(tree):
                forbidden = any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_MODULES
                ) or (
                    module == "email"
                    and name in ("mime", "header")
                    or name in FORBIDDEN_NAMES.get(module, ())
                )
                if forbidden:
                    offences.append(f"{source}: {module}{'.' + name if name else ''}")

        self.assertEqual(offences, [])

    def test_messages_are_parsed_with_the_modern_policy(self):
        """`message_from_bytes` without a policy is the legacy parser."""
        offences = []
        for source, tree in _sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                called = getattr(function, "attr", getattr(function, "id", ""))
                if called in ("message_from_bytes", "message_from_binary_file") and (
                    "policy" not in {keyword.arg for keyword in node.keywords}
                ):
                    offences.append(f"{source}:{node.lineno}")

        self.assertEqual(offences, [])
