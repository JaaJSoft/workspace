"""Only two places may talk to an SMTP server.

The mail module sends as the user, through the user's own account; the core
email service sends as the instance. Anything else that opens a connection of
its own bypasses the rate limits, the suppression list and the send record -
precisely the safeguards that keep the instance off blocklists.
"""

import ast
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]

ALLOWED = (
    WORKSPACE / "mail",
    WORKSPACE / "core" / "services" / "email.py",
)

FORBIDDEN_MODULES = ("smtplib", "django.core.mail")


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            # `from django.core import mail`
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _is_allowed(path):
    return any(path == allowed or allowed in path.parents for allowed in ALLOWED)


class EmailBoundaryTests(unittest.TestCase):
    def test_no_smtp_outside_mail_and_the_email_service(self):
        offenders = []
        for path in sorted(WORKSPACE.rglob("*.py")):
            if "tests" in path.relative_to(WORKSPACE).parts or _is_allowed(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module in _imported_modules(tree):
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_MODULES
                ):
                    offenders.append(f"{path.relative_to(WORKSPACE)}: {module}")
        self.assertEqual(
            offenders,
            [],
            "send through workspace.core.services.email.send_email instead",
        )

    def test_the_scan_sees_the_allowed_senders(self):
        # Guards the walk itself: a path bug that scanned nothing would pass.
        tree = ast.parse(ALLOWED[1].read_text(encoding="utf-8"))
        self.assertIn("django.core.mail", set(_imported_modules(tree)))
