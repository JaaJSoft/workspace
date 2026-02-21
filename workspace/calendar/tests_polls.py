from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember, Poll, PollSlot, PollVote

User = get_user_model()


class PollTestMixin:
    def setUp(self):
        self.owner = User.objects.create_user(
            username='pollowner', email='pollowner@test.com', password='pass123',
        )
        self.voter = User.objects.create_user(
            username='voter', email='voter@test.com', password='pass123',
        )
        self.calendar = Calendar.objects.create(
            name='Work', owner=self.owner,
        )


class PollCRUDTests(PollTestMixin, APITestCase):
    def test_create_poll(self):
        self.client.force_authenticate(user=self.owner)
        tomorrow = (timezone.now() + timedelta(days=1)).isoformat()
        day_after = (timezone.now() + timedelta(days=2)).isoformat()
        resp = self.client.post('/api/v1/calendar/polls', {
            'title': 'Team lunch',
            'slots': [
                {'start': tomorrow},
                {'start': day_after},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['title'], 'Team lunch')
        self.assertEqual(len(resp.data['slots']), 2)
        self.assertIn('share_token', resp.data)

    def test_create_poll_requires_min_2_slots(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/v1/calendar/polls', {
            'title': 'Bad poll',
            'slots': [{'start': timezone.now().isoformat()}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_polls(self):
        self.client.force_authenticate(user=self.owner)
        Poll.objects.create(title='P1', created_by=self.owner)
        Poll.objects.create(title='P2', created_by=self.voter)
        resp = self.client.get('/api/v1/calendar/polls')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_delete_poll(self):
        self.client.force_authenticate(user=self.owner)
        poll = Poll.objects.create(title='Delete me', created_by=self.owner)
        resp = self.client.delete(f'/api/v1/calendar/polls/{poll.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Poll.objects.filter(uuid=poll.uuid).exists())

    def test_cannot_delete_others_poll(self):
        self.client.force_authenticate(user=self.voter)
        poll = Poll.objects.create(title='Not yours', created_by=self.owner)
        resp = self.client.delete(f'/api/v1/calendar/polls/{poll.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class PollVoteTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll = Poll.objects.create(title='Lunch', created_by=self.owner)
        self.slot1 = PollSlot.objects.create(
            poll=self.poll,
            start=timezone.now() + timedelta(days=1),
            position=0,
        )
        self.slot2 = PollSlot.objects.create(
            poll=self.poll,
            start=timezone.now() + timedelta(days=2),
            position=1,
        )

    def test_authenticated_vote(self):
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [
                {'slot_id': str(self.slot1.uuid), 'choice': 'yes'},
                {'slot_id': str(self.slot2.uuid), 'choice': 'no'},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(PollVote.objects.filter(user=self.voter).count(), 2)

    def test_vote_updates_existing(self):
        self.client.force_authenticate(user=self.voter)
        PollVote.objects.create(slot=self.slot1, user=self.voter, choice='no')
        self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
        }, format='json')
        vote = PollVote.objects.get(slot=self.slot1, user=self.voter)
        self.assertEqual(vote.choice, 'yes')

    def test_cannot_vote_on_closed_poll(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class GuestVoteTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll = Poll.objects.create(title='Lunch', created_by=self.owner)
        self.slot = PollSlot.objects.create(
            poll=self.poll,
            start=timezone.now() + timedelta(days=1),
            position=0,
        )

    def test_guest_vote_via_shared_link(self):
        resp = self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Alice',
                'guest_email': 'alice@ext.com',
                'votes': [{'slot_id': str(self.slot.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        vote = PollVote.objects.get(guest_name='Alice')
        self.assertEqual(vote.choice, 'yes')
        self.assertIsNone(vote.user)

    def test_shared_link_get(self):
        resp = self.client.get(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Lunch')


class PollFinalizeTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll = Poll.objects.create(title='Lunch', created_by=self.owner)
        self.slot = PollSlot.objects.create(
            poll=self.poll,
            start=timezone.now() + timedelta(days=1),
            end=timezone.now() + timedelta(days=1, hours=1),
            position=0,
        )
        PollVote.objects.create(slot=self.slot, user=self.voter, choice='yes')

    def test_finalize_creates_event(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.status, 'closed')
        self.assertIsNotNone(self.poll.event)
        event = Event.objects.get(uuid=self.poll.event.uuid)
        self.assertEqual(event.title, 'Lunch')
        self.assertEqual(event.start, self.slot.start)
        self.assertTrue(
            EventMember.objects.filter(event=event, user=self.voter).exists()
        )

    def test_only_creator_can_finalize(self):
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_finalize_closed_poll(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
