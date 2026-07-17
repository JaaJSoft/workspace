from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.event_search import search_events_qs
from workspace.common.search import fts5_available

User = get_user_model()


def make_event(calendar, title, *, description="", location="", **kwargs):
    return Event.objects.create(
        calendar=calendar,
        owner=calendar.owner,
        title=title,
        description=description,
        location=location,
        start=kwargs.pop("start", timezone.now()),
        **kwargs,
    )


class FtsSchemaTests(TestCase):
    def test_sqlite_fts_table_exists(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only schema check")
        with connection.cursor() as c:
            c.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='calendar_event_fts'"
            )
            self.assertIsNotNone(c.fetchone())

    def test_sqlite_triggers_track_insert_update_delete(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only trigger check")
        user = User.objects.create_user(username="t", email="t@x.io")
        cal = Calendar.objects.create(name="Cal", owner=user)
        event = make_event(cal, "the zanzibar kickoff")

        def match(term):
            with connection.cursor() as c:
                c.execute(
                    "SELECT rowid FROM calendar_event_fts "
                    "WHERE calendar_event_fts MATCH %s",
                    (f'"{term}"',),
                )
                return c.fetchone()

        self.assertIsNotNone(match("zanzibar"))

        event.title = "the yokohama kickoff"
        event.save(update_fields=["title"])
        self.assertIsNone(match("zanzibar"))
        self.assertIsNotNone(match("yokohama"))

        event.delete()
        self.assertIsNone(match("yokohama"))


class EventSearchServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", email="al@x.io")
        cls.bob = User.objects.create_user(username="bob", email="bo@x.io")
        cls.cal_alice = Calendar.objects.create(name="Personal", owner=cls.alice)
        cls.cal_bob = Calendar.objects.create(name="Bob", owner=cls.bob)
        cls.visible = make_event(
            cls.cal_alice,
            "Kumquat tasting",
            description="bring the samples",
            location="Room 42",
        )
        cls.hidden = make_event(cls.cal_bob, "Secret kumquat sync")

    def test_finds_by_title(self):
        hits = list(search_events_qs(self.alice, "kumquat"))
        self.assertEqual([e.uuid for e in hits], [self.visible.uuid])

    def test_finds_by_description(self):
        hits = list(search_events_qs(self.alice, "samples"))
        self.assertEqual([e.uuid for e in hits], [self.visible.uuid])

    def test_finds_by_location(self):
        hits = list(search_events_qs(self.alice, "Room 42"))
        self.assertEqual([e.uuid for e in hits], [self.visible.uuid])

    def test_access_control_excludes_foreign_calendars(self):
        self.assertEqual(len(list(search_events_qs(self.bob, "kumquat"))), 1)
        self.assertEqual(len(list(search_events_qs(self.alice, "kumquat"))), 1)

    def test_event_membership_grants_visibility(self):
        from workspace.calendar.models import EventMember

        EventMember.objects.create(event=self.hidden, user=self.alice)
        hits = [e.uuid for e in search_events_qs(self.alice, "kumquat")]
        self.assertIn(self.hidden.uuid, hits)

    def test_cancelled_events_excluded(self):
        Event.objects.filter(pk=self.visible.pk).update(is_cancelled=True)
        self.assertEqual(list(search_events_qs(self.alice, "kumquat")), [])

    def test_recurrence_exceptions_excluded(self):
        make_event(
            self.cal_alice,
            "Kumquat tasting (moved)",
            recurrence_parent=self.visible,
            original_start=self.visible.start,
        )
        hits = list(search_events_qs(self.alice, "kumquat"))
        self.assertEqual([e.uuid for e in hits], [self.visible.uuid])

    def test_title_outranks_description(self):
        if connection.vendor == "sqlite" and not fts5_available():
            self.skipTest("relevance ranking needs FTS5 on SQLite")
        in_description = make_event(
            self.cal_alice, "Weekly", description="pretzel pretzel pretzel agenda"
        )
        in_title = make_event(self.cal_alice, "Pretzel workshop")
        hits = [e.uuid for e in search_events_qs(self.alice, "pretzel")]
        self.assertLess(hits.index(in_title.uuid), hits.index(in_description.uuid))

    def test_equal_rank_falls_back_to_latest_start(self):
        earlier = make_event(self.cal_alice, "Same walrus title", start=timezone.now())
        later = make_event(
            self.cal_alice,
            "Same walrus title",
            start=timezone.now() + timedelta(days=1),
        )
        hits = [e.uuid for e in search_events_qs(self.alice, "walrus")]
        self.assertEqual(hits, [later.uuid, earlier.uuid])

    def test_accent_insensitive(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required for the accent path")
        make_event(self.cal_alice, "Réunion générale")
        hits = list(search_events_qs(self.alice, "reunion"))
        self.assertEqual(len(hits), 1)

    def test_blank_query_returns_no_rows(self):
        self.assertEqual(list(search_events_qs(self.alice, "   ")), [])

    def test_malformed_query_does_not_crash(self):
        hits = list(search_events_qs(self.alice, 'kumquat" -sync'))
        self.assertIsInstance(hits, list)


class ProviderAndToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="palice", email="pa@x.io")
        cls.cal = Calendar.objects.create(name="Work", owner=cls.alice)
        cls.event = make_event(
            cls.cal,
            "Sprint review",
            description="bring the flamingo mockups",
            location="Dock B",
        )

    def test_provider_matches_description(self):
        # The provider used to be a title-only icontains; a word that only
        # appears in the description must now match.
        from workspace.calendar.search import search_events

        results = search_events("flamingo", self.alice, 10)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.uuid, str(self.event.uuid))
        self.assertEqual(r.name, "Sprint review")
        self.assertEqual(r.tags[0].label, "Work")

    def test_ai_tool_matches_location(self):
        from workspace.calendar.ai_tools import CalendarToolProvider, SearchEventsParams

        provider = CalendarToolProvider()
        args = SearchEventsParams(query="Dock B")
        result = provider.search_events(
            args, user=self.alice, bot=None, conversation_id=None, context={}
        )
        self.assertIn("Sprint review", result)
