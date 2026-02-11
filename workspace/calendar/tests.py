import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, CalendarSubscription, Event, EventMember
from workspace.calendar.search import search_events

User = get_user_model()


class CalendarTestMixin:
    """Common setup for calendar tests."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@test.com', password='pass123',
        )
        self.member = User.objects.create_user(
            username='member', email='member@test.com', password='pass123',
        )
        self.outsider = User.objects.create_user(
            username='outsider', email='outsider@test.com', password='pass123',
        )

        self.calendar = Calendar.objects.create(
            name='Work', owner=self.owner,
        )

        self.event = Event.objects.create(
            calendar=self.calendar,
            title='Team Meeting',
            start=timezone.now() + timedelta(days=1),
            end=timezone.now() + timedelta(days=1, hours=1),
            owner=self.owner,
        )
        EventMember.objects.create(event=self.event, user=self.member)


# ---------- Calendar CRUD ----------


class CalendarListTests(CalendarTestMixin, APITestCase):
    """Tests for GET /api/v1/calendar/calendars"""

    url = '/api/v1/calendar/calendars'

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_owned_calendars(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['owned']), 1)
        self.assertEqual(resp.data['owned'][0]['name'], 'Work')

    def test_list_includes_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['subscribed']), 1)
        self.assertEqual(resp.data['subscribed'][0]['name'], 'Work')

    def test_does_not_include_unrelated_calendars(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['owned']), 0)
        self.assertEqual(len(resp.data['subscribed']), 0)


class CalendarCreateTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/calendar/calendars"""

    url = '/api/v1/calendar/calendars'

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, {'name': 'New'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {'name': 'Personal'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'Personal')
        self.assertEqual(resp.data['owner']['username'], 'owner')

    def test_create_calendar_default_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {'name': 'Default Color'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['color'], 'primary')

    def test_create_calendar_custom_color(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {'name': 'Custom', 'color': 'accent'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['color'], 'accent')

    def test_create_calendar_missing_name(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarDetailTests(CalendarTestMixin, APITestCase):
    """Tests for PUT/DELETE /api/v1/calendar/calendars/<id>"""

    def url(self, calendar_id):
        return f'/api/v1/calendar/calendars/{calendar_id}'

    def test_update_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.calendar.uuid),
            {'name': 'Renamed', 'color': 'secondary'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Renamed')
        self.assertEqual(resp.data['color'], 'secondary')

    def test_update_calendar_not_owner_returns_404(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(
            self.url(self.calendar.uuid),
            {'name': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_nonexistent_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(uuid.uuid4()),
            {'name': 'Ghost'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_calendar(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Calendar.objects.filter(uuid=self.calendar.uuid).exists())

    def test_delete_calendar_not_owner_returns_404(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.calendar.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_calendar_cascades_events(self):
        self.client.force_authenticate(self.owner)
        event_id = self.event.uuid
        self.client.delete(self.url(self.calendar.uuid))
        self.assertFalse(Event.objects.filter(uuid=event_id).exists())


# ---------- Event CRUD ----------


class EventListTests(CalendarTestMixin, APITestCase):
    """Tests for GET /api/v1/calendar/events"""

    url = '/api/v1/calendar/events'

    def _range_params(self, days_before=7, days_after=7):
        start = (timezone.now() - timedelta(days=days_before)).isoformat()
        end = (timezone.now() + timedelta(days=days_after)).isoformat()
        return {'start': start, 'end': end}

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url, self._range_params())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_start_and_end_params(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.get(self.url, {'start': timezone.now().isoformat()})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.get(self.url, {'end': timezone.now().isoformat()})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_events_in_range(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e['title'] for e in resp.data]
        self.assertIn('Team Meeting', titles)

    def test_excludes_events_outside_range(self):
        # Create an event far in the future
        Event.objects.create(
            calendar=self.calendar,
            title='Far Future',
            start=timezone.now() + timedelta(days=365),
            end=timezone.now() + timedelta(days=365, hours=1),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        titles = [e['title'] for e in resp.data]
        self.assertNotIn('Far Future', titles)

    def test_includes_events_from_subscribed_calendars(self):
        CalendarSubscription.objects.create(user=self.member, calendar=self.calendar)
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url, self._range_params())
        titles = [e['title'] for e in resp.data]
        self.assertIn('Team Meeting', titles)

    def test_includes_events_where_user_is_member(self):
        # member is invited to self.event via setUp
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url, self._range_params())
        titles = [e['title'] for e in resp.data]
        self.assertIn('Team Meeting', titles)

    def test_filter_by_calendar_ids(self):
        other_cal = Calendar.objects.create(name='Other', owner=self.owner)
        Event.objects.create(
            calendar=other_cal,
            title='Other Event',
            start=timezone.now() + timedelta(hours=1),
            end=timezone.now() + timedelta(hours=2),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        params = self._range_params()
        params['calendar_ids'] = str(other_cal.uuid)
        resp = self.client.get(self.url, params)
        titles = [e['title'] for e in resp.data]
        self.assertIn('Other Event', titles)
        self.assertNotIn('Team Meeting', titles)

    def test_all_day_event_in_range(self):
        Event.objects.create(
            calendar=self.calendar,
            title='All Day',
            start=timezone.now() + timedelta(days=2),
            end=None,
            all_day=True,
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url, self._range_params())
        titles = [e['title'] for e in resp.data]
        self.assertIn('All Day', titles)


class EventCreateTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/calendar/events"""

    url = '/api/v1/calendar/events'

    def _event_data(self, **overrides):
        data = {
            'calendar_id': str(self.calendar.uuid),
            'title': 'New Event',
            'start': (timezone.now() + timedelta(days=2)).isoformat(),
            'end': (timezone.now() + timedelta(days=2, hours=1)).isoformat(),
        }
        data.update(overrides)
        return data

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, self._event_data(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, self._event_data(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['title'], 'New Event')
        self.assertEqual(resp.data['owner']['username'], 'owner')

    def test_create_event_with_members(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(member_ids=[self.member.id])
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m['user']['username'] for m in resp.data['members']]
        self.assertIn('member', member_usernames)

    def test_create_event_owner_excluded_from_members(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(member_ids=[self.owner.id, self.member.id])
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        member_usernames = [m['user']['username'] for m in resp.data['members']]
        self.assertNotIn('owner', member_usernames)
        self.assertIn('member', member_usernames)

    def test_create_event_all_day(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(all_day=True, end=None)
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['all_day'])

    def test_create_event_missing_title(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data()
        del data['title']
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_calendar_not_owned(self):
        outsider_cal = Calendar.objects.create(name='Outsider Cal', owner=self.outsider)
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar_id=str(outsider_cal.uuid))
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_event_nonexistent_calendar(self):
        self.client.force_authenticate(self.owner)
        data = self._event_data(calendar_id=str(uuid.uuid4()))
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class EventDetailTests(CalendarTestMixin, APITestCase):
    """Tests for GET/PUT/DELETE /api/v1/calendar/events/<id>"""

    def url(self, event_id):
        return f'/api/v1/calendar/events/{event_id}'

    # --- GET ---

    def test_get_event_as_owner(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Team Meeting')

    def test_get_event_as_member(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_get_event_as_subscriber(self):
        CalendarSubscription.objects.create(user=self.outsider, calendar=self.calendar)
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_get_event_no_access(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_nonexistent_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- PUT ---

    def test_update_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'title': 'Updated Meeting'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Updated Meeting')

    def test_update_event_partial_fields(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'description': 'Added description'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['description'], 'Added description')
        # title unchanged
        self.assertEqual(resp.data['title'], 'Team Meeting')

    def test_update_event_change_calendar(self):
        new_cal = Calendar.objects.create(name='Personal', owner=self.owner)
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'calendar_id': str(new_cal.uuid)},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['calendar_id'], str(new_cal.uuid))

    def test_update_event_add_members(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'member_ids': [self.member.id, self.outsider.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        member_usernames = [m['user']['username'] for m in resp.data['members']]
        self.assertIn('outsider', member_usernames)
        self.assertIn('member', member_usernames)

    def test_update_event_remove_members(self):
        self.client.force_authenticate(self.owner)
        # Remove all members
        resp = self.client.put(
            self.url(self.event.uuid),
            {'member_ids': []},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['members']), 0)

    def test_update_event_not_owner_returns_403(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'title': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- DELETE ---

    def test_delete_event(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Event.objects.filter(uuid=self.event.uuid).exists())

    def test_delete_event_not_owner_returns_403(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ---------- Event Respond ----------


class EventRespondTests(CalendarTestMixin, APITestCase):
    """Tests for POST /api/v1/calendar/events/<id>/respond"""

    def url(self, event_id):
        return f'/api/v1/calendar/events/{event_id}/respond'

    def test_unauthenticated_rejected(self):
        resp = self.client.post(
            self.url(self.event.uuid), {'status': 'accepted'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_accept_invitation(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid), {'status': 'accepted'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'accepted')
        membership = EventMember.objects.get(event=self.event, user=self.member)
        self.assertEqual(membership.status, EventMember.Status.ACCEPTED)

    def test_decline_invitation(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid), {'status': 'declined'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'declined')

    def test_not_invited_returns_403(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            self.url(self.event.uuid), {'status': 'accepted'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_status_rejected(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid), {'status': 'maybe'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------- Search ----------


class SearchTests(CalendarTestMixin, APITestCase):
    """Tests for search_events() function."""

    def test_search_finds_owned_calendar_events(self):
        results = search_events('Team', self.owner, limit=10)
        names = [r.name for r in results]
        self.assertIn('Team Meeting', names)

    def test_search_finds_subscribed_calendar_events(self):
        CalendarSubscription.objects.create(user=self.outsider, calendar=self.calendar)
        results = search_events('Team', self.outsider, limit=10)
        names = [r.name for r in results]
        self.assertIn('Team Meeting', names)

    def test_search_finds_events_where_member(self):
        results = search_events('Team', self.member, limit=10)
        names = [r.name for r in results]
        self.assertIn('Team Meeting', names)

    def test_search_excludes_inaccessible_events(self):
        results = search_events('Team', self.outsider, limit=10)
        self.assertEqual(len(results), 0)

    def test_search_filters_by_title(self):
        Event.objects.create(
            calendar=self.calendar,
            title='Lunch Break',
            start=timezone.now() + timedelta(hours=3),
            owner=self.owner,
        )
        results = search_events('Lunch', self.owner, limit=10)
        names = [r.name for r in results]
        self.assertIn('Lunch Break', names)
        self.assertNotIn('Team Meeting', names)

    def test_search_respects_limit(self):
        for i in range(5):
            Event.objects.create(
                calendar=self.calendar,
                title=f'Event {i}',
                start=timezone.now() + timedelta(hours=i),
                owner=self.owner,
            )
        results = search_events('Event', self.owner, limit=3)
        self.assertEqual(len(results), 3)
