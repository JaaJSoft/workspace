import importlib
from datetime import UTC, datetime, timedelta

from django.test import SimpleTestCase

# The module name starts with a digit, so it is not a valid identifier and
# cannot be reached with a plain `from ... import`.
backfill = importlib.import_module(
    "workspace.calendar.migrations.0018_backfill_recurrence_rule"
)


class BackfillTests(SimpleTestCase):
    """The old columns are a strict subset of RRULE, so the forward fill is
    lossless. These cases pin the exact text it produces.

    SimpleTestCase and no fixtures on purpose: these are pure functions, and
    a database the test never queries only slows the suite down.
    """

    def test_weekly_with_interval(self):
        self.assertEqual(
            backfill.rule_for("weekly", 2, None), "RRULE:FREQ=WEEKLY;INTERVAL=2"
        )

    def test_interval_of_one_is_omitted(self):
        self.assertEqual(backfill.rule_for("daily", 1, None), "RRULE:FREQ=DAILY")

    def test_recurrence_end_becomes_until(self):
        self.assertEqual(
            backfill.rule_for("daily", 1, datetime(2026, 3, 1, 9, tzinfo=UTC)),
            "RRULE:FREQ=DAILY;UNTIL=20260301T090000Z",
        )

    def test_non_recurring_row_gets_a_blank_rule(self):
        self.assertEqual(backfill.rule_for(None, 1, None), "")

    def test_until_bound_includes_the_duration(self):
        # recurrence_until is the END of the last occurrence, so a two-hour
        # event bounded at 10:00 must report 12:00.
        self.assertEqual(
            backfill.until_for(
                datetime(2026, 3, 1, 10, tzinfo=UTC), timedelta(hours=2)
            ),
            datetime(2026, 3, 1, 12, tzinfo=UTC),
        )
