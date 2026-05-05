from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Event, EventMember
from workspace.notifications.models import Notification

from .test_calendar import CalendarTestMixin


# ---------- Notifications ----------


class CalendarNotificationTestBase(CalendarTestMixin, APITestCase):
    """Common helpers for calendar notification tests."""

    def _notifs_for(self, user, origin='calendar'):
        return Notification.objects.filter(recipient=user, origin=origin)



class EventCreateNotificationTests(CalendarNotificationTestBase):
    """Tests for notifications when creating events with members."""

    url = '/api/v1/calendar/events'

    def test_create_event_with_members_notifies_them(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {
            'calendar_id': str(self.calendar.uuid),
            'title': 'Planning',
            'start': (timezone.now() + timedelta(days=1)).isoformat(),
            'end': (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
            'member_ids': [self.member.id],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Invited', notifs.first().title)
        self.assertEqual(notifs.first().actor, self.owner)

    def test_create_event_without_members_no_notification(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self.url, {
            'calendar_id': str(self.calendar.uuid),
            'title': 'Solo',
            'start': (timezone.now() + timedelta(days=1)).isoformat(),
            'end': (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(Notification.objects.filter(origin='calendar').count(), 0)

    def test_create_event_owner_not_notified(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self.url, {
            'calendar_id': str(self.calendar.uuid),
            'title': 'Planning',
            'start': (timezone.now() + timedelta(days=1)).isoformat(),
            'end': (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
            'member_ids': [self.member.id, self.owner.id],
        }, format='json')
        self.assertEqual(self._notifs_for(self.owner).count(), 0)



class EventUpdateNotificationTests(CalendarNotificationTestBase):
    """Tests for notifications when updating events."""

    def url(self, event_id):
        return f'/api/v1/calendar/events/{event_id}'

    def test_update_event_notifies_members(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.put(
            self.url(self.event.uuid),
            {'title': 'Renamed Meeting'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('was updated', notifs.first().title)
        self.assertEqual(notifs.first().actor, self.owner)

    def test_update_event_does_not_notify_owner(self):
        self.client.force_authenticate(self.owner)
        self.client.put(
            self.url(self.event.uuid),
            {'title': 'Renamed'},
            format='json',
        )
        self.assertEqual(self._notifs_for(self.owner).count(), 0)

    def test_add_member_notifies_new_member(self):
        self.client.force_authenticate(self.owner)
        self.client.put(
            self.url(self.event.uuid),
            {'member_ids': [self.member.id, self.outsider.id]},
            format='json',
        )
        notifs = self._notifs_for(self.outsider)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Invited', notifs.first().title)

    def test_remove_member_notifies_removed_user(self):
        self.client.force_authenticate(self.owner)
        self.client.put(
            self.url(self.event.uuid),
            {'member_ids': []},
            format='json',
        )
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Removed', notifs.first().title)



class EventDeleteNotificationTests(CalendarNotificationTestBase):
    """Tests for notifications when deleting events."""

    def url(self, event_id):
        return f'/api/v1/calendar/events/{event_id}'

    def test_delete_event_notifies_members(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url(self.event.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('cancelled', n.title)
        self.assertIn('Team Meeting', n.title)
        self.assertEqual(n.actor, self.owner)

    def test_delete_event_does_not_notify_owner(self):
        self.client.force_authenticate(self.owner)
        self.client.delete(self.url(self.event.uuid))
        self.assertEqual(self._notifs_for(self.owner).count(), 0)

    def test_delete_event_without_members_no_notification(self):
        event_no_members = Event.objects.create(
            calendar=self.calendar,
            title='Solo Event',
            start=timezone.now() + timedelta(days=2),
            end=timezone.now() + timedelta(days=2, hours=1),
            owner=self.owner,
        )
        self.client.force_authenticate(self.owner)
        self.client.delete(self.url(event_no_members.uuid))
        self.assertEqual(Notification.objects.filter(origin='calendar').count(), 0)

    def test_delete_event_notifies_multiple_members(self):
        EventMember.objects.create(event=self.event, user=self.outsider)
        self.client.force_authenticate(self.owner)
        self.client.delete(self.url(self.event.uuid))
        self.assertEqual(self._notifs_for(self.member).count(), 1)
        self.assertEqual(self._notifs_for(self.outsider).count(), 1)



class EventRespondNotificationTests(CalendarNotificationTestBase):
    """Tests for notifications when responding to an invitation."""

    def url(self, event_id):
        return f'/api/v1/calendar/events/{event_id}/respond'

    def test_accept_notifies_owner(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid),
            {'status': 'accepted'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.owner)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('accepted', n.title)
        self.assertIn('Team Meeting', n.title)
        self.assertEqual(n.actor, self.member)

    def test_decline_notifies_owner(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(
            self.url(self.event.uuid),
            {'status': 'declined'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.owner)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('declined', notifs.first().title)

    def test_owner_respond_no_self_notification(self):
        """If the owner is somehow also a member, responding should not self-notify."""
        EventMember.objects.create(event=self.event, user=self.owner)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url(self.event.uuid),
            {'status': 'accepted'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._notifs_for(self.owner).count(), 0)
