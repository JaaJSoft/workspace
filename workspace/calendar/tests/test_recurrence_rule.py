from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase

from workspace.calendar.services import recurrence_rule as rr


class ParseTests(SimpleTestCase):
    def test_blank_rule_is_not_a_series(self):
        self.assertIsNone(rr.parse("", datetime(2026, 1, 6, 10, tzinfo=UTC)))

    def test_unparseable_rule_returns_none_instead_of_raising(self):
        # A malformed rule must never 500 a calendar view.
        self.assertIsNone(
            rr.parse("RRULE:FREQ=NONSENSE", datetime(2026, 1, 6, 10, tzinfo=UTC))
        )

    def test_unparseable_rule_error_message_is_scrubbed_before_logging(self):
        # The parser's own exception can echo back attacker-controlled
        # substrings from the rule text; logging it unscrubbed is a log
        # injection vector (CWE-117) even though rule_text itself is scrubbed.
        hostile = ValueError("bad token\r\nFAKE-LOG-LINE: admin logged in")
        with patch(
            "workspace.calendar.services.recurrence_rule.rrulestr",
            side_effect=hostile,
        ):
            with self.assertLogs(
                "workspace.calendar.services.recurrence_rule", level="WARNING"
            ) as cm:
                self.assertIsNone(
                    rr.parse("RRULE:FREQ=DAILY", datetime(2026, 1, 6, 10, tzinfo=UTC))
                )
        self.assertEqual(len(cm.output), 1)
        self.assertNotIn("\r", cm.output[0])
        self.assertNotIn("\n", cm.output[0])
        self.assertIn("FAKE-LOG-LINE", cm.output[0])

    def test_full_grammar_is_honoured(self):
        rule = (
            "RRULE:FREQ=MONTHLY;BYDAY=2TU;COUNT=5\n"
            "RDATE:20260401T090000Z\n"
            "EXDATE:20260310T100000Z"
        )
        occurrences = list(rr.parse(rule, datetime(2026, 1, 6, 10, tzinfo=UTC)))
        self.assertEqual(
            [o.date().isoformat() for o in occurrences],
            ["2026-01-13", "2026-02-10", "2026-04-01", "2026-04-14", "2026-05-12"],
        )


class LastOccurrenceEndTests(SimpleTestCase):
    def setUp(self):
        self.dtstart = datetime(2026, 1, 6, 10, tzinfo=UTC)

    def test_infinite_rule_has_no_bound(self):
        self.assertIsNone(rr.last_occurrence_end("RRULE:FREQ=DAILY", self.dtstart))

    def test_until_rule_is_bounded(self):
        end = rr.last_occurrence_end(
            "RRULE:FREQ=DAILY;UNTIL=20260110T100000Z", self.dtstart
        )
        self.assertEqual(end, datetime(2026, 1, 10, 10, tzinfo=UTC))

    def test_count_rule_is_bounded_without_flattening_the_rule(self):
        end = rr.last_occurrence_end("RRULE:FREQ=DAILY;COUNT=3", self.dtstart)
        self.assertEqual(end, datetime(2026, 1, 8, 10, tzinfo=UTC))

    def test_bound_is_the_end_of_the_last_occurrence_not_its_start(self):
        # This is what makes the value a safe upper bound for overlap
        # pruning: an occurrence starting before a window can still run
        # into it.
        end = rr.last_occurrence_end(
            "RRULE:FREQ=DAILY;COUNT=3", self.dtstart, duration=timedelta(hours=2)
        )
        self.assertEqual(end, datetime(2026, 1, 8, 12, tzinfo=UTC))

    def test_one_unbounded_rule_makes_the_whole_set_unbounded(self):
        rule = "RRULE:FREQ=DAILY;COUNT=3\nRRULE:FREQ=WEEKLY"
        self.assertIsNone(rr.last_occurrence_end(rule, self.dtstart))

    def test_hostile_rule_degrades_to_a_loose_bound_instead_of_hanging(self):
        rule = "RRULE:FREQ=SECONDLY;UNTIL=20990101T000000Z"
        end = rr.last_occurrence_end(rule, self.dtstart)
        # Not the exact last occurrence, but never earlier than it.
        self.assertEqual(end, datetime(2099, 1, 1, tzinfo=UTC))

    def test_mixed_count_and_until_rrules_never_underestimate(self):
        # The COUNT-only line's true last occurrence lands around
        # 2026-05-02 - far past 2026-02-01, the largest literal UNTIL in the
        # rule. Reporting that literal would be an under-estimate; None
        # (unbounded) is the only answer that is never earlier than the
        # truth.
        rule = (
            "RRULE:FREQ=SECONDLY;COUNT=10000000\n"
            "RRULE:FREQ=YEARLY;UNTIL=20260201T000000Z"
        )
        self.assertIsNone(rr.last_occurrence_end(rule, self.dtstart))


class SimpleRoundTripTests(SimpleTestCase):
    def test_from_simple_omits_interval_of_one(self):
        self.assertEqual(rr.from_simple("weekly"), "RRULE:FREQ=WEEKLY")

    def test_from_simple_emits_interval_and_until(self):
        self.assertEqual(
            rr.from_simple("daily", 2, datetime(2026, 3, 1, 9, tzinfo=UTC)),
            "RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20260301T090000Z",
        )

    def test_to_simple_round_trips(self):
        self.assertEqual(
            rr.to_simple("RRULE:FREQ=DAILY;INTERVAL=2"),
            {"frequency": "daily", "interval": 2, "until": None},
        )

    def test_to_simple_refuses_anything_the_picker_cannot_express(self):
        # None is what puts the web UI into read-only mode. Erring strict
        # here is what stops a phone-authored rule being silently dropped
        # on the next save from the web modal.
        for rule in (
            "RRULE:FREQ=MONTHLY;BYDAY=2TU",
            "RRULE:FREQ=DAILY;COUNT=5",
            "RRULE:FREQ=DAILY\nRDATE:20260401T090000Z",
            "RRULE:FREQ=DAILY\nRRULE:FREQ=WEEKLY",
        ):
            with self.subTest(rule=rule):
                self.assertIsNone(rr.to_simple(rule))


class TruncateBeforeTests(SimpleTestCase):
    def test_adds_until(self):
        self.assertEqual(
            rr.truncate_before(
                "RRULE:FREQ=WEEKLY", datetime(2026, 5, 1, 9, tzinfo=UTC)
            ),
            "RRULE:FREQ=WEEKLY;UNTIL=20260501T090000Z",
        )

    def test_replaces_an_existing_until(self):
        self.assertEqual(
            rr.truncate_before(
                "RRULE:FREQ=WEEKLY;UNTIL=20261231T000000Z",
                datetime(2026, 5, 1, 9, tzinfo=UTC),
            ),
            "RRULE:FREQ=WEEKLY;UNTIL=20260501T090000Z",
        )

    def test_count_is_dropped_because_rfc5545_forbids_count_with_until(self):
        result = rr.truncate_before(
            "RRULE:FREQ=WEEKLY;COUNT=10", datetime(2026, 5, 1, 9, tzinfo=UTC)
        )
        self.assertEqual(result, "RRULE:FREQ=WEEKLY;UNTIL=20260501T090000Z")
        self.assertNotIn("COUNT", result)

    def test_rdates_after_the_cut_are_dropped(self):
        result = rr.truncate_before(
            "RRULE:FREQ=WEEKLY\nRDATE:20260401T090000Z,20260601T090000Z",
            datetime(2026, 5, 1, 9, tzinfo=UTC),
        )
        self.assertIn("RDATE:20260401T090000Z", result)
        self.assertNotIn("20260601T090000Z", result)

    def test_rdate_with_tzid_param_is_still_recognised_and_kept(self):
        # RFC 5545 3.8.5.2 allows a TZID parameter on RDATE. The property
        # name in the raw line is "RDATE;TZID=America/New_York", not
        # "RDATE" - matching on the full string used to miss this branch
        # entirely and pass the line through unmodified.
        result = rr.truncate_before(
            "RRULE:FREQ=WEEKLY\nRDATE;TZID=America/New_York:20260401T090000",
            datetime(2026, 4, 1, 18, tzinfo=UTC),
        )
        self.assertIn("RDATE;TZID=America/New_York:20260401T090000", result)

    def test_rdate_with_tzid_is_compared_in_local_time_not_utc(self):
        # 09:00 America/New_York on 2026-04-01 is 13:00 UTC (EDT, UTC-4). A
        # comparison that read the literal digits as UTC would consider it
        # before a 12:30 UTC cutoff on the same date; it is actually after.
        result = rr.truncate_before(
            "RRULE:FREQ=WEEKLY\nRDATE;TZID=America/New_York:20260401T090000",
            datetime(2026, 4, 1, 12, 30, tzinfo=UTC),
        )
        self.assertNotIn("RDATE", result)


class DescribeTests(SimpleTestCase):
    def test_common_patterns_read_as_english(self):
        self.assertEqual(rr.describe("RRULE:FREQ=WEEKLY"), "Every week")
        self.assertEqual(rr.describe("RRULE:FREQ=DAILY;INTERVAL=3"), "Every 3 days")

    def test_unknown_rule_falls_back_to_the_raw_text(self):
        # An honest fallback beats a confident mistranslation: the user
        # sees a rule they cannot edit here, not a wrong summary.
        rule = "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU"
        self.assertEqual(rr.describe(rule), rule)


class SimpleSteppingTests(SimpleTestCase):
    def test_fixed_step_rules_are_anchorable(self):
        self.assertTrue(rr.is_simple_stepping("RRULE:FREQ=DAILY;INTERVAL=2"))
        self.assertTrue(rr.is_simple_stepping("RRULE:FREQ=WEEKLY"))

    def test_by_parts_and_calendar_stepping_are_not(self):
        # The dtstart re-anchoring optimization assumes fixed timedelta
        # steps; these rules break that assumption.
        for rule in (
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
            "RRULE:FREQ=MONTHLY",
            "RRULE:FREQ=DAILY\nRDATE:20260401T090000Z",
            # COUNT is measured from dtstart: re-anchoring dtstart forward
            # would fabricate occurrences past the series' real end.
            "RRULE:FREQ=DAILY;COUNT=10",
        ):
            with self.subTest(rule=rule):
                self.assertFalse(rr.is_simple_stepping(rule))


class ClientCorpusTests(SimpleTestCase):
    """Rule shapes captured from real clients must all be parseable.

    Every rule below is a shape a real calendar client emits. This is the
    test that makes CalDAV interop possible: our engine has to parse each
    one and produce a usable bound (or a confirmed infinity) without
    raising, regardless of how exotic the BYxxx combination is. Whether the
    stored text matches the client's byte-for-byte is asserted separately,
    where the storage layer exists to check it against.
    """

    CORPUS = [
        "RRULE:FREQ=DAILY",
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
        "RRULE:FREQ=MONTHLY;BYDAY=2TU",
        "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "RRULE:FREQ=WEEKLY;WKST=SU;COUNT=12;BYDAY=MO",
        "RRULE:FREQ=MONTHLY;BYSETPOS=-1;BYDAY=MO,TU,WE,TH,FR",
        "RRULE:FREQ=DAILY;UNTIL=20261231T235959Z",
        "RRULE:FREQ=DAILY;INTERVAL=2\nRDATE:20260401T090000Z",
    ]

    def test_every_corpus_rule_parses(self):
        dtstart = datetime(2026, 1, 6, 10, tzinfo=UTC)
        for rule in self.CORPUS:
            with self.subTest(rule=rule):
                self.assertIsNotNone(rr.parse(rule, dtstart))

    def test_every_corpus_rule_yields_a_bound_or_infinity_without_error(self):
        dtstart = datetime(2026, 1, 6, 10, tzinfo=UTC)
        for rule in self.CORPUS:
            with self.subTest(rule=rule):
                rr.last_occurrence_end(rule, dtstart, timedelta(hours=1))


class _StubEvent:
    """Minimal duck-typed stand-in for Event.

    ``apply_rule`` reads/writes ``recurrence_rule``, ``is_recurring``,
    ``recurrence_until``, ``start``, ``end``, ``timezone`` and - to keep the
    legacy query layer and expansion engine in step until they are migrated -
    ``recurrence_frequency``, ``recurrence_interval`` and ``recurrence_end``.
    """

    def __init__(self, start, end=None, timezone=""):
        self.start = start
        self.end = end
        self.timezone = timezone
        self.recurrence_rule = ""
        self.is_recurring = False
        self.recurrence_until = None
        self.recurrence_frequency = None
        self.recurrence_interval = 1
        self.recurrence_end = None


class ApplyRuleTests(SimpleTestCase):
    def test_sets_all_three_fields_coherently(self):
        event = _StubEvent(
            start=datetime(2026, 1, 6, 10, tzinfo=UTC),
            end=datetime(2026, 1, 6, 11, tzinfo=UTC),
        )
        rr.apply_rule(event, "RRULE:FREQ=DAILY;COUNT=3")
        self.assertEqual(event.recurrence_rule, "RRULE:FREQ=DAILY;COUNT=3")
        self.assertTrue(event.is_recurring)
        self.assertEqual(event.recurrence_until, datetime(2026, 1, 8, 11, tzinfo=UTC))

    def test_clearing_the_rule_clears_the_bound(self):
        event = _StubEvent(start=datetime(2026, 1, 6, 10, tzinfo=UTC))
        rr.apply_rule(event, "RRULE:FREQ=DAILY;COUNT=3")
        rr.apply_rule(event, "")
        self.assertEqual(event.recurrence_rule, "")
        self.assertFalse(event.is_recurring)
        self.assertIsNone(event.recurrence_until)

    def test_simple_rule_mirrors_into_legacy_columns(self):
        # The query layer and the expansion engine still read these columns
        # (they are not migrated to the rule yet), so every apply_rule call
        # must keep them in step for a rule they can express.
        event = _StubEvent(start=datetime(2026, 1, 6, 10, tzinfo=UTC))
        rr.apply_rule(event, "RRULE:FREQ=WEEKLY;INTERVAL=2")
        self.assertEqual(event.recurrence_frequency, "weekly")
        self.assertEqual(event.recurrence_interval, 2)
        self.assertIsNone(event.recurrence_end)

    def test_complex_rule_stays_recurring_while_legacy_columns_go_none(self):
        # BYDAY has no legacy equivalent: the row is recurring by
        # is_recurring, it is just invisible to the legacy-gated readers
        # until they are migrated - never resurrected as non-recurring.
        event = _StubEvent(start=datetime(2026, 1, 6, 10, tzinfo=UTC))
        rr.apply_rule(event, "RRULE:FREQ=MONTHLY;BYDAY=2TU")
        self.assertTrue(event.is_recurring)
        self.assertIsNone(event.recurrence_frequency)
        self.assertEqual(event.recurrence_interval, 1)
        self.assertIsNone(event.recurrence_end)

    def test_clearing_the_rule_also_clears_legacy_columns(self):
        event = _StubEvent(start=datetime(2026, 1, 6, 10, tzinfo=UTC))
        rr.apply_rule(event, "RRULE:FREQ=WEEKLY")
        rr.apply_rule(event, "")
        self.assertIsNone(event.recurrence_frequency)
        self.assertEqual(event.recurrence_interval, 1)
        self.assertIsNone(event.recurrence_end)


class DeriveIntoDefaultsTests(SimpleTestCase):
    def test_matches_apply_rule_for_an_equivalent_zoned_series(self):
        # Spans the 2026 US spring-forward (Mar 8): a computation that
        # ignored the zone would disagree with a zone-aware one by exactly
        # one hour, which is what pins the two functions to the same answer.
        start = datetime(2026, 3, 7, 15, tzinfo=UTC)
        end = datetime(2026, 3, 7, 16, tzinfo=UTC)
        rule = "RRULE:FREQ=DAILY;COUNT=3"

        event = _StubEvent(start=start, end=end, timezone="America/New_York")
        rr.apply_rule(event, rule)

        defaults = {
            "recurrence_rule": rule,
            "start": start,
            "end": end,
            "timezone": "America/New_York",
        }
        rr.derive_into_defaults(defaults)

        self.assertEqual(defaults["is_recurring"], event.is_recurring)
        self.assertEqual(defaults["recurrence_until"], event.recurrence_until)
        self.assertEqual(
            defaults["recurrence_until"], datetime(2026, 3, 9, 15, tzinfo=UTC)
        )
        self.assertEqual(defaults["recurrence_frequency"], event.recurrence_frequency)
        self.assertEqual(defaults["recurrence_interval"], event.recurrence_interval)
        self.assertEqual(defaults["recurrence_end"], event.recurrence_end)

    def test_complex_rule_leaves_legacy_columns_none(self):
        defaults = {
            "recurrence_rule": "RRULE:FREQ=MONTHLY;BYDAY=2TU",
            "start": datetime(2026, 1, 6, 10, tzinfo=UTC),
            "end": None,
            "timezone": "",
        }
        rr.derive_into_defaults(defaults)
        self.assertTrue(defaults["is_recurring"])
        self.assertIsNone(defaults["recurrence_frequency"])
        self.assertEqual(defaults["recurrence_interval"], 1)
        self.assertIsNone(defaults["recurrence_end"])
