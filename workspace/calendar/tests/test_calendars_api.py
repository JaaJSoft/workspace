import uuid
from unittest.mock import patch

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, CalendarSubscription, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.serializers import CalendarSerializer

from .test_calendar import CalendarTestMixin

FEED = "https://example.com/feed.ics"


class CalendarListTests(CalendarTestMixin, APITestCase):
    """Tests for GET /api/v1/calendars"""

    url = "/api/v1/calendars"

    def _names(self, user, **params):
        self.client.force_authenticate(user)
        resp = self.client.get(self.url, params)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return [calendar["name"] for calendar in resp.data]

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_lists_owned_and_subscribed_calendars_as_one_array(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        Calendar.objects.create(name="Home", owner=self.member)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [(c["name"], c["is_owned"]) for c in resp.data],
            [("Home", True), ("Work", False)],
        )
        self.assertEqual(resp.data[1]["owner"]["username"], "owner")

    def test_does_not_include_unrelated_calendars(self):
        self.assertEqual(self._names(self.outsider), [])

    def test_an_external_calendar_is_one_of_the_list(self):
        feed = Calendar.objects.create(name="Holidays", owner=self.owner)
        ExternalCalendar.objects.create(calendar=feed, url=FEED, last_error="Gone")
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        by_name = {calendar["name"]: calendar for calendar in resp.data}
        self.assertEqual(sorted(by_name), ["Holidays", "Work"])
        self.assertTrue(by_name["Holidays"]["is_external"])
        self.assertEqual(by_name["Holidays"]["source_url"], FEED)
        self.assertEqual(by_name["Holidays"]["sync_error"], "Gone")
        self.assertIsNone(by_name["Holidays"]["last_synced_at"])
        # A calendar written by hand carries the same keys, empty.
        self.assertFalse(by_name["Work"]["is_external"])
        self.assertIsNone(by_name["Work"]["source_url"])
        self.assertEqual(by_name["Work"]["sync_error"], "")
        self.assertEqual(set(by_name["Work"]), set(by_name["Holidays"]))

    def test_filters(self):
        feed = Calendar.objects.create(name="Holidays", owner=self.owner)
        ExternalCalendar.objects.create(calendar=feed, url=FEED)
        theirs = Calendar.objects.create(name="Theirs", owner=self.outsider)
        CalendarSubscription.objects.create(user=self.owner, calendar=theirs)

        self.assertEqual(self._names(self.owner, external="true"), ["Holidays"])
        self.assertEqual(self._names(self.owner, external="false"), ["Theirs", "Work"])
        self.assertEqual(self._names(self.owner, owner="me"), ["Holidays", "Work"])
        self.assertEqual(self._names(self.owner, owner_username="OUTSIDER"), ["Theirs"])
        self.assertEqual(self._names(self.owner, q="holi"), ["Holidays"])

    def test_a_filter_value_that_cannot_be_read_is_refused(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, {"external": "sometimes"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["external"])


class CalendarCreateTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/calendars"""

    url = "/api/v1/calendars"

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, {"name": "New"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"name": "Personal"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["name"], "Personal")
        self.assertEqual(resp.data["owner"]["username"], "owner")
        self.assertTrue(resp.data["is_owned"])
        # The answer of a create is the object a read answers.
        read = self.client.get(f"{self.url}/{resp.data['uuid']}")
        self.assertEqual(read.data, resp.data)

    def test_create_calendar_default_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"name": "Default Color"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["color"], "primary")

    def test_create_calendar_custom_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url, {"name": "Custom", "color": "accent"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["color"], "accent")

    def test_create_calendar_missing_name(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarDetailTests(CalendarTestMixin, APITestCase):
    """Tests for GET/PATCH/DELETE /api/v1/calendars/<uuid>"""

    def url(self, calendar_id):
        return f"/api/v1/calendars/{calendar_id}"

    def test_get_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Work")

    def test_update_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            self.url(self.calendar.uuid),
            {"name": "Renamed", "color": "secondary"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Renamed")
        self.assertEqual(resp.data["color"], "secondary")

    def test_update_changes_only_what_is_named(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            self.url(self.calendar.uuid), {"color": "accent"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Work")
        self.assertEqual(resp.data["color"], "accent")

    def test_put_is_not_offered(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.calendar.uuid), {"name": "Renamed"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_a_rename_keeps_a_sync_revision_bumped_meanwhile(self):
        """An event change between the rename's read and its write must not be
        rolled back: CalDAV clients would see the sync token go backwards."""
        validate = CalendarSerializer.is_valid

        def event_changed_meanwhile(serializer, *args, **kwargs):
            Calendar.objects.filter(pk=self.calendar.pk).update(sync_revision=7)
            return validate(serializer, *args, **kwargs)

        self.client.force_authenticate(self.owner)
        with patch.object(
            CalendarSerializer,
            "is_valid",
            autospec=True,
            side_effect=event_changed_meanwhile,
        ):
            resp = self.client.patch(
                self.url(self.calendar.uuid), {"name": "Renamed"}, format="json"
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Renamed")
        self.assertEqual(self.calendar.sync_revision, 7)

    def test_a_calendar_out_of_reach_answers_like_one_that_does_not_exist(self):
        """The member is invited to an event of the calendar, no more."""
        self.client.force_authenticate(self.member)
        for method, body in (
            ("get", None),
            ("patch", {"name": "Hacked"}),
            ("delete", None),
        ):
            with self.subTest(method=method):
                call = getattr(self.client, method)
                hidden = call(self.url(self.calendar.uuid), body, format="json")
                absent = call(self.url(uuid.uuid4()), body, format="json")
                self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (absent.status_code, absent.content),
                )
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Work")

    def test_a_subscriber_reads_a_calendar_and_cannot_change_it(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        self.client.force_authenticate(self.member)
        read = self.client.get(self.url(self.calendar.uuid))
        self.assertEqual(read.status_code, status.HTTP_200_OK)
        self.assertFalse(read.data["is_owned"])
        patch_ = self.client.patch(
            self.url(self.calendar.uuid), {"name": "Hacked"}, format="json"
        )
        delete = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(patch_.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(delete.status_code, status.HTTP_403_FORBIDDEN)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Work")

    def test_delete_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Calendar.objects.filter(uuid=self.calendar.uuid).exists())

    def test_delete_calendar_cascades_events(self):
        self.client.force_authenticate(self.owner)
        event_id = self.event.uuid
        self.client.delete(self.url(self.calendar.uuid))
        self.assertFalse(Event.objects.filter(uuid=event_id).exists())


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
@patch("workspace.calendar.services.calendars.sync_external_calendar_task")
class ExternalCalendarTests(CalendarTestMixin, APITestCase):
    """An external calendar: a calendar created with the ICS feed that fills it."""

    url = "/api/v1/calendars"

    def _external(self, owner=None):
        calendar = Calendar.objects.create(name="Holidays", owner=owner or self.owner)
        return ExternalCalendar.objects.create(calendar=calendar, url=FEED)

    def test_create_with_a_feed_starts_its_first_sync(self, task):
        self.client.force_authenticate(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                self.url, {"name": "Holidays", "source_url": FEED}, format="json"
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["is_external"])
        self.assertEqual(resp.data["source_url"], FEED)
        source = ExternalCalendar.objects.get(calendar_id=resp.data["uuid"])
        self.assertEqual(source.url, FEED)
        task.apply_async.assert_called_once()
        self.assertEqual(task.apply_async.call_args.args[0], [str(source.uuid)])

    def test_a_feed_on_an_internal_address_is_refused(self, task):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url,
            {"name": "Inside", "source_url": "http://127.0.0.1/feed.ics"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["source_url"])
        self.assertFalse(Calendar.objects.filter(name="Inside").exists())
        task.apply_async.assert_not_called()

    def test_the_feed_of_a_calendar_cannot_be_changed(self, task):
        source = self._external()
        plain = self.calendar
        self.client.force_authenticate(self.owner)
        for calendar in (source.calendar, plain):
            with self.subTest(calendar=calendar.name):
                resp = self.client.patch(
                    f"{self.url}/{calendar.uuid}",
                    {"source_url": "https://example.com/other.ics"},
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(resp.data), ["source_url"])
        source.refresh_from_db()
        self.assertEqual(source.url, FEED)
        self.assertFalse(ExternalCalendar.objects.filter(calendar=plain).exists())

    def test_renaming_an_external_calendar_keeps_its_feed(self, task):
        source = self._external()
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"{self.url}/{source.calendar_id}",
            {"name": "Bank holidays", "source_url": FEED},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "Bank holidays")
        self.assertEqual(resp.data["source_url"], FEED)

    def test_deleting_an_external_calendar_drops_its_feed(self, task):
        source = self._external()
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(f"{self.url}/{source.calendar_id}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ExternalCalendar.objects.filter(pk=source.pk).exists())

    def test_sync_asks_for_the_feed_to_be_read_again(self, task):
        source = self._external()
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"{self.url}/{source.calendar_id}/sync")
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.content, b"")
        task.apply_async.assert_called_once()
        self.assertEqual(task.apply_async.call_args.args[0], [str(source.uuid)])

    def test_sync_is_refused_on_a_calendar_with_no_feed(self, task):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"{self.url}/{self.calendar.uuid}/sync")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        task.apply_async.assert_not_called()

    def test_the_feed_is_its_owners_to_know(self, task):
        """A feed address often carries the secret that opens it: someone
        subscribed to the calendar reads its events, not where they come
        from nor why the last reading failed."""
        source = self._external()
        ExternalCalendar.objects.filter(pk=source.pk).update(last_error="401 for t0ken")
        CalendarSubscription.objects.create(user=self.member, calendar=source.calendar)

        # The page embeds the same list: a session, as a browser holds one.
        self.client.force_login(self.member)
        for resp in (
            self.client.get(f"{self.url}/{source.calendar_id}"),
            self.client.get(self.url),
            self.client.get("/calendar"),
        ):
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertNotIn(FEED, resp.content.decode())
            self.assertNotIn("t0ken", resp.content.decode())
        read = self.client.get(f"{self.url}/{source.calendar_id}").data
        self.assertTrue(read["is_external"])
        self.assertIsNone(read["source_url"])
        self.assertEqual(read["sync_error"], "")

        self.client.force_login(self.owner)
        own = self.client.get(f"{self.url}/{source.calendar_id}").data
        self.assertEqual(own["source_url"], FEED)
        self.assertEqual(own["sync_error"], "401 for t0ken")

    def test_sync_is_the_owners(self, task):
        source = self._external()
        CalendarSubscription.objects.create(user=self.member, calendar=source.calendar)
        self.client.force_authenticate(self.member)
        subscriber = self.client.post(f"{self.url}/{source.calendar_id}/sync")
        self.assertEqual(subscriber.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.outsider)
        hidden = self.client.post(f"{self.url}/{source.calendar_id}/sync")
        absent = self.client.post(f"{self.url}/{uuid.uuid4()}/sync")
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        task.apply_async.assert_not_called()
