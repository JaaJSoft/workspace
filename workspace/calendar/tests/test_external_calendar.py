from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.calendar.services.ics_sync import sync_external_calendar

User = get_user_model()

# ─── ICS fixtures ───────────────────────────────────────────

ICS_FEED = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-event-1@example.com\r\n"
    "DTSTART:20260401T100000Z\r\n"
    "DTEND:20260401T110000Z\r\n"
    "SUMMARY:External Meeting\r\n"
    "DESCRIPTION:A synced event\r\n"
    "LOCATION:Remote\r\n"
    "SEQUENCE:0\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-event-2@example.com\r\n"
    "DTSTART:20260402T140000Z\r\n"
    "DTEND:20260402T150000Z\r\n"
    "SUMMARY:External Standup\r\n"
    "SEQUENCE:1\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_FEED_UPDATED = ICS_FEED.replace(
    "SUMMARY:External Meeting", "SUMMARY:External Meeting (v2)"
).replace("SEQUENCE:0", "SEQUENCE:1")

ICS_FEED_REMOVED = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-event-2@example.com\r\n"
    "DTSTART:20260402T140000Z\r\n"
    "DTEND:20260402T150000Z\r\n"
    "SUMMARY:External Standup\r\n"
    "SEQUENCE:1\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_ALL_DAY = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-allday@example.com\r\n"
    "DTSTART;VALUE=DATE:20260501\r\n"
    "DTEND;VALUE=DATE:20260502\r\n"
    "SUMMARY:Holiday\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_RECURRING = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-recurring@example.com\r\n"
    "DTSTART:20260401T090000Z\r\n"
    "DTEND:20260401T100000Z\r\n"
    "SUMMARY:Daily Sync\r\n"
    "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=10\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_WITH_ORGANIZER = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-organized@example.com\r\n"
    "DTSTART:20260601T100000Z\r\n"
    "DTEND:20260601T110000Z\r\n"
    "SUMMARY:Organized Meeting\r\n"
    "ORGANIZER;CN=External Boss:mailto:boss@external.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_NO_ORGANIZER = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:ext-anonymous@example.com\r\n"
    "DTSTART:20260602T100000Z\r\n"
    "DTEND:20260602T110000Z\r\n"
    "SUMMARY:Anonymous Meeting\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _mock_response(text, status_code=200, etag=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {'ETag': etag} if etag else {}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_httpx(mock_httpx, response):
    """Configure mock_httpx.Client to return the given response."""
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = response
    mock_httpx.Client.return_value = mock_client
    return mock_client


# ─── Model Tests ────────────────────────────────────────────


class ExternalCalendarModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.calendar = Calendar.objects.create(name='Google', owner=self.user)

    def test_create_external_calendar(self):
        ext = ExternalCalendar.objects.create(
            calendar=self.calendar,
            url='https://calendar.google.com/calendar/ical/test/basic.ics',
        )
        self.assertIsNotNone(ext.uuid)
        self.assertEqual(ext.calendar, self.calendar)
        self.assertTrue(ext.is_active)
        self.assertEqual(ext.sync_interval, 900)
        self.assertIsNone(ext.last_synced_at)

    def test_onetoone_relationship(self):
        ExternalCalendar.objects.create(
            calendar=self.calendar,
            url='https://example.com/feed.ics',
        )
        self.calendar.refresh_from_db()
        self.assertIsNotNone(self.calendar.external_source)

    def test_str_representation(self):
        ext = ExternalCalendar.objects.create(
            calendar=self.calendar,
            url='https://example.com/feed.ics',
        )
        self.assertIn('Google', str(ext))


# ─── Sync Service Tests ────────────────────────────────────


class SyncExternalCalendarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='sync_user', email='sync@test.com', password='pass123',
        )
        self.calendar = Calendar.objects.create(name='External Feed', owner=self.user)
        self.ext = ExternalCalendar.objects.create(
            calendar=self.calendar,
            url='https://example.com/feed.ics',
        )

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_creates_events(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response(ICS_FEED, etag='"v1"'))

        sync_external_calendar(self.ext)

        events = Event.objects.filter(calendar=self.calendar).order_by('start')
        self.assertEqual(events.count(), 2)
        self.assertEqual(events[0].title, 'External Meeting')
        self.assertEqual(events[0].ical_uid, 'ext-event-1@example.com')
        self.assertEqual(events[1].title, 'External Standup')

        self.ext.refresh_from_db()
        self.assertIsNotNone(self.ext.last_synced_at)
        self.assertEqual(self.ext.last_etag, '"v1"')
        self.assertEqual(self.ext.last_error, '')

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_updates_existing_events(self, mock_httpx):
        client = _mock_httpx(mock_httpx, _mock_response(ICS_FEED))
        sync_external_calendar(self.ext)

        # Second sync with updated title
        client.get.return_value = _mock_response(ICS_FEED_UPDATED)
        self.ext.last_etag = ''
        self.ext.save()
        sync_external_calendar(self.ext)

        event = Event.objects.get(ical_uid='ext-event-1@example.com', calendar=self.calendar)
        self.assertEqual(event.title, 'External Meeting (v2)')
        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 2)

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_removes_deleted_events(self, mock_httpx):
        client = _mock_httpx(mock_httpx, _mock_response(ICS_FEED))
        sync_external_calendar(self.ext)
        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 2)

        client.get.return_value = _mock_response(ICS_FEED_REMOVED)
        self.ext.last_etag = ''
        self.ext.save()
        sync_external_calendar(self.ext)

        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 1)
        self.assertTrue(Event.objects.filter(ical_uid='ext-event-2@example.com').exists())
        self.assertFalse(Event.objects.filter(ical_uid='ext-event-1@example.com').exists())

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_skips_on_304(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response('', status_code=304))

        self.ext.last_etag = '"v1"'
        self.ext.save()
        sync_external_calendar(self.ext)

        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 0)
        self.ext.refresh_from_db()
        self.assertIsNotNone(self.ext.last_synced_at)

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_all_day_event(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response(ICS_ALL_DAY))

        sync_external_calendar(self.ext)

        event = Event.objects.get(ical_uid='ext-allday@example.com')
        self.assertTrue(event.all_day)
        self.assertEqual(event.title, 'Holiday')

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_recurring_event(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response(ICS_RECURRING))

        sync_external_calendar(self.ext)

        event = Event.objects.get(ical_uid='ext-recurring@example.com')
        self.assertEqual(event.recurrence_frequency, 'daily')
        self.assertEqual(event.recurrence_interval, 1)
        # COUNT=10 daily from April 1 → last occurrence April 10
        self.assertIsNotNone(event.recurrence_end)
        self.assertEqual(event.recurrence_end.day, 10)
        self.assertEqual(event.recurrence_end.month, 4)

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_parses_organizer_email(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response(ICS_WITH_ORGANIZER))

        sync_external_calendar(self.ext)

        event = Event.objects.get(ical_uid='ext-organized@example.com')
        self.assertEqual(event.external_organizer, 'boss@external.com')

    @patch('workspace.calendar.services.ics_sync.httpx')
    def test_sync_without_organizer_defaults_to_empty(self, mock_httpx):
        _mock_httpx(mock_httpx, _mock_response(ICS_NO_ORGANIZER))

        sync_external_calendar(self.ext)

        event = Event.objects.get(ical_uid='ext-anonymous@example.com')
        self.assertEqual(event.external_organizer, '')


# ─── Celery Task Tests ─────────────────────────────────────


class SyncTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='task_user', email='task@test.com', password='pass123',
        )
        self.calendar = Calendar.objects.create(name='Task Cal', owner=self.user)
        self.ext = ExternalCalendar.objects.create(
            calendar=self.calendar,
            url='https://example.com/feed.ics',
        )

    @patch('workspace.calendar.services.ics_sync.sync_external_calendar')
    def test_sync_single_task(self, mock_sync):
        from workspace.calendar.tasks import sync_external_calendar_task
        sync_external_calendar_task(str(self.ext.uuid))
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args[0][0].uuid, self.ext.uuid)

    @patch('workspace.calendar.tasks.sync_external_calendar_task')
    def test_sync_all_dispatches_active(self, mock_task):
        mock_task.delay = MagicMock()
        cal2 = Calendar.objects.create(name='Inactive', owner=self.user)
        ExternalCalendar.objects.create(
            calendar=cal2, url='https://example.com/inactive.ics', is_active=False,
        )

        from workspace.calendar.tasks import sync_all_external_calendars
        sync_all_external_calendars()

        # Only the active one should be dispatched
        mock_task.delay.assert_called_once_with(str(self.ext.uuid))

    @patch('workspace.calendar.services.ics_sync.sync_external_calendar')
    def test_sync_task_records_error(self, mock_sync):
        mock_sync.side_effect = Exception('Network error')
        from workspace.calendar.tasks import sync_external_calendar_task

        with self.assertRaises(Exception):
            sync_external_calendar_task(str(self.ext.uuid))

        self.ext.refresh_from_db()
        self.assertIn('Network error', self.ext.last_error)


# ─── API Tests ──────────────────────────────────────────────


class ExternalCalendarAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='api_user', email='api@test.com', password='pass123',
        )
        self.other = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )
        self.url = '/api/v1/calendar/external-calendars'

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    @patch('workspace.calendar.views_external.sync_external_calendar_task')
    def test_create_external_calendar(self, mock_task):
        mock_task.delay = MagicMock()

        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {
            'url': 'https://example.com/feed.ics',
            'name': 'My External',
            'color': 'blue',
        })
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'My External')
        self.assertTrue(resp.data['is_external'])
        self.assertIn('external_source', resp.data)
        mock_task.delay.assert_called_once()

    def test_list_external_calendars(self):
        cal = Calendar.objects.create(name='Ext1', owner=self.user)
        ExternalCalendar.objects.create(calendar=cal, url='https://example.com/1.ics')
        cal2 = Calendar.objects.create(name='Ext2', owner=self.other)
        ExternalCalendar.objects.create(calendar=cal2, url='https://example.com/2.ics')

        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'Ext1')

    def test_update_external_calendar(self):
        cal = Calendar.objects.create(name='Old Name', owner=self.user)
        ext = ExternalCalendar.objects.create(calendar=cal, url='https://example.com/1.ics')

        self.client.force_authenticate(self.user)
        resp = self.client.put(
            f'{self.url}/{ext.uuid}',
            {'name': 'New Name', 'color': 'red'},
        )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        cal.refresh_from_db()
        self.assertEqual(cal.name, 'New Name')
        self.assertEqual(cal.color, 'red')

    def test_delete_external_calendar(self):
        cal = Calendar.objects.create(name='ToDelete', owner=self.user)
        ext = ExternalCalendar.objects.create(calendar=cal, url='https://example.com/del.ics')

        self.client.force_authenticate(self.user)
        resp = self.client.delete(f'{self.url}/{ext.uuid}')
        self.assertEqual(resp.status_code, http_status.HTTP_204_NO_CONTENT)
        self.assertFalse(Calendar.objects.filter(pk=cal.pk).exists())

    def test_cannot_access_others_external_calendar(self):
        cal = Calendar.objects.create(name='Private', owner=self.other)
        ext = ExternalCalendar.objects.create(calendar=cal, url='https://example.com/priv.ics')

        self.client.force_authenticate(self.user)
        resp = self.client.put(f'{self.url}/{ext.uuid}', {'name': 'Hacked'})
        self.assertEqual(resp.status_code, http_status.HTTP_404_NOT_FOUND)

    @patch('workspace.calendar.views_external.sync_external_calendar_task')
    def test_manual_sync(self, mock_task):
        mock_task.delay = MagicMock()
        cal = Calendar.objects.create(name='SyncMe', owner=self.user)
        ext = ExternalCalendar.objects.create(calendar=cal, url='https://example.com/sync.ics')

        self.client.force_authenticate(self.user)
        resp = self.client.post(f'{self.url}/{ext.uuid}/sync')
        self.assertEqual(resp.status_code, http_status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once_with(str(ext.uuid))


# ─── Event Card View Tests ───────────────────────────────────


class EventCardExternalTests(TestCase):
    """Verify the event-card partial shows the external organiser."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='card_user', email='card@test.com', password='pass123',
        )
        self.cal = Calendar.objects.create(name='Feed', owner=self.user)
        ExternalCalendar.objects.create(
            calendar=self.cal, url='https://example.com/c.ics',
        )

    def test_event_card_shows_external_organizer(self):
        from django.utils import timezone
        event = Event.objects.create(
            calendar=self.cal, title='Synced', owner=self.user,
            start=timezone.now(),
            end=timezone.now(),
            external_organizer='boss@ext.com',
            ical_uid='card@x',
        )
        self.client.force_login(self.user)
        resp = self.client.get(f'/calendar/events/{event.pk}/card')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'boss@ext.com', resp.content)
        self.assertNotIn(b'card_user', resp.content)  # no workspace username shown

    def test_event_card_falls_back_to_calendar_name(self):
        from django.utils import timezone
        self.cal.name = 'Fallback Anchor Calendar 12345'
        self.cal.save(update_fields=['name'])
        event = Event.objects.create(
            calendar=self.cal, title='Anonymous Sync', owner=self.user,
            start=timezone.now(),
            end=timezone.now(),
            external_organizer='',
            ical_uid='card2@x',
        )
        self.client.force_login(self.user)
        resp = self.client.get(f'/calendar/events/{event.pk}/card')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Fallback Anchor Calendar 12345', resp.content)
        self.assertNotIn(b'boss@ext.com', resp.content)

    def test_event_card_native_shows_owner_username(self):
        """A native (non-external) event shows the workspace owner's username."""
        from django.utils import timezone
        native_cal = Calendar.objects.create(name='Native Cal', owner=self.user)
        event = Event.objects.create(
            calendar=native_cal, title='Native Event', owner=self.user,
            start=timezone.now(),
            end=timezone.now(),
        )
        self.client.force_login(self.user)
        resp = self.client.get(f'/calendar/events/{event.pk}/card')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'card_user', resp.content)  # owner username rendered
