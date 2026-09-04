import re
from pathlib import Path

from django.test import SimpleTestCase

CALENDAR = Path(__file__).resolve().parent.parent
OWNER = CALENDAR / "services" / "recurrence_rule.py"

COLUMNS = "is_recurring|recurrence_until"

# Attribute assignment (`event.is_recurring = True`) and dict-subscript
# assignment (`defaults["recurrence_until"] = ...`). Deliberately NOT a bare
# `\bname\s*=`: that also matches ORM keyword arguments such as
# `filter(is_recurring=True)`, which are reads, not writes, and appear all
# over the query layer.
ASSIGNMENTS = (
    re.compile(rf"\.\s*({COLUMNS})\s*=(?!=)"),
    re.compile(rf"\[[\"']({COLUMNS})[\"']\]\s*=(?!=)"),
)


class DerivedColumnOwnershipTests(SimpleTestCase):
    """`is_recurring` and `recurrence_until` are derived from the rule text.

    A writer that sets them by hand puts them out of step with the rule, and
    the failure is silent - the event simply stops being expanded. Ownership is
    enforced here rather than left to review.

    Known limit: a constructor keyword argument (`Event(is_recurring=True)`) is
    textually identical to an ORM filter keyword and is not detected. The
    calendar_recheck_recurrence command is the second net for that case.
    """

    def test_only_the_rule_service_assigns_the_derived_columns(self):
        offenders = []
        for path in CALENDAR.rglob("*.py"):
            if path == OWNER or "/migrations/" in path.as_posix():
                continue
            if "/tests/" in path.as_posix():
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if line.lstrip().startswith("#"):
                    continue
                if any(pattern.search(line) for pattern in ASSIGNMENTS):
                    offenders.append(
                        f"{path.relative_to(CALENDAR)}:{number}: {line.strip()}"
                    )
        self.assertEqual(
            offenders,
            [],
            "Assign these through services.recurrence_rule.apply_rule():\n"
            + "\n".join(offenders),
        )
