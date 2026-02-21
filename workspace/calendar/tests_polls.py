from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import (
    Calendar, Event, EventMember, Poll, PollInvitee, PollSlot, PollVote,
)

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

    def _make_poll_with_slots(self, **kwargs):
        poll = Poll.objects.create(
            title=kwargs.get('title', 'Test poll'),
            created_by=kwargs.get('created_by', self.owner),
        )
        slot1 = PollSlot.objects.create(
            poll=poll, start=timezone.now() + timedelta(days=1), position=0,
        )
        slot2 = PollSlot.objects.create(
            poll=poll, start=timezone.now() + timedelta(days=2), position=1,
        )
        return poll, slot1, slot2


# ── CRUD ──────────────────────────────────────────────────────────

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

    def test_create_poll_with_description_and_end_times(self):
        self.client.force_authenticate(user=self.owner)
        start1 = (timezone.now() + timedelta(days=1)).isoformat()
        end1 = (timezone.now() + timedelta(days=1, hours=2)).isoformat()
        start2 = (timezone.now() + timedelta(days=2)).isoformat()
        resp = self.client.post('/api/v1/calendar/polls', {
            'title': 'Meeting',
            'description': 'Weekly sync',
            'slots': [
                {'start': start1, 'end': end1},
                {'start': start2},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['description'], 'Weekly sync')
        slots = resp.data['slots']
        self.assertIsNotNone(slots[0]['end'])
        self.assertIsNone(slots[1]['end'])

    def test_create_poll_requires_min_2_slots(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/v1/calendar/polls', {
            'title': 'Bad poll',
            'slots': [{'start': timezone.now().isoformat()}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_poll_requires_auth(self):
        resp = self.client.post('/api/v1/calendar/polls', {
            'title': 'Nope',
            'slots': [
                {'start': timezone.now().isoformat()},
                {'start': (timezone.now() + timedelta(days=1)).isoformat()},
            ],
        }, format='json')
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_list_polls(self):
        self.client.force_authenticate(user=self.owner)
        Poll.objects.create(title='P1', created_by=self.owner)
        Poll.objects.create(title='P2', created_by=self.voter)
        resp = self.client.get('/api/v1/calendar/polls')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_list_polls_filter_status_closed(self):
        self.client.force_authenticate(user=self.owner)
        Poll.objects.create(title='Open', created_by=self.owner, status='open')
        Poll.objects.create(title='Closed', created_by=self.owner, status='closed')
        resp = self.client.get('/api/v1/calendar/polls?status=closed')
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['title'], 'Closed')

    def test_list_polls_filter_status_all(self):
        self.client.force_authenticate(user=self.owner)
        Poll.objects.create(title='Open', created_by=self.owner, status='open')
        Poll.objects.create(title='Closed', created_by=self.owner, status='closed')
        resp = self.client.get('/api/v1/calendar/polls?status=all')
        self.assertEqual(len(resp.data), 2)

    def test_list_polls_filter_shared(self):
        """Polls where user voted or is invited but didn't create."""
        self.client.force_authenticate(user=self.voter)
        poll, slot1, _ = self._make_poll_with_slots()
        PollVote.objects.create(slot=slot1, user=self.voter, choice='yes')
        resp = self.client.get('/api/v1/calendar/polls?filter=shared')
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['title'], 'Test poll')

    def test_list_polls_filter_shared_via_invite(self):
        """Polls where user is invited (no vote yet) appear in shared filter."""
        self.client.force_authenticate(user=self.voter)
        poll, _, _ = self._make_poll_with_slots()
        PollInvitee.objects.create(poll=poll, user=self.voter)
        resp = self.client.get('/api/v1/calendar/polls?filter=shared')
        self.assertEqual(len(resp.data), 1)

    def test_list_polls_filter_shared_excludes_own(self):
        """Own polls don't appear in shared filter even if voted."""
        self.client.force_authenticate(user=self.owner)
        poll, slot1, _ = self._make_poll_with_slots()
        PollVote.objects.create(slot=slot1, user=self.owner, choice='yes')
        resp = self.client.get('/api/v1/calendar/polls?filter=shared')
        self.assertEqual(len(resp.data), 0)

    def test_get_poll_detail(self):
        self.client.force_authenticate(user=self.voter)
        poll, _, _ = self._make_poll_with_slots()
        resp = self.client.get(f'/api/v1/calendar/polls/{poll.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Test poll')
        self.assertEqual(len(resp.data['slots']), 2)

    def test_update_poll(self):
        self.client.force_authenticate(user=self.owner)
        poll = Poll.objects.create(title='Old title', created_by=self.owner)
        resp = self.client.patch(
            f'/api/v1/calendar/polls/{poll.uuid}',
            {'title': 'New title'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        poll.refresh_from_db()
        self.assertEqual(poll.title, 'New title')

    def test_cannot_update_others_poll(self):
        self.client.force_authenticate(user=self.voter)
        poll = Poll.objects.create(title='Not yours', created_by=self.owner)
        resp = self.client.patch(
            f'/api/v1/calendar/polls/{poll.uuid}',
            {'title': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

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


# ── Authenticated Votes ──────────────────────────────────────────

class PollVoteTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll, self.slot1, self.slot2 = self._make_poll_with_slots()

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

    def test_vote_ignores_invalid_slot(self):
        self.client.force_authenticate(user=self.voter)
        other_poll, other_slot, _ = self._make_poll_with_slots(title='Other')
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [{'slot_id': str(other_slot.uuid), 'choice': 'yes'}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(PollVote.objects.filter(user=self.voter).count(), 0)

    def test_cannot_vote_on_closed_poll(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_vote_requires_auth(self):
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
        }, format='json')
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_vote_response_includes_counts(self):
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(f'/api/v1/calendar/polls/{self.poll.uuid}/vote', {
            'votes': [
                {'slot_id': str(self.slot1.uuid), 'choice': 'yes'},
                {'slot_id': str(self.slot2.uuid), 'choice': 'maybe'},
            ],
        }, format='json')
        slots = {s['uuid']: s for s in resp.data['slots']}
        self.assertEqual(slots[str(self.slot1.uuid)]['yes_count'], 1)
        self.assertEqual(slots[str(self.slot2.uuid)]['maybe_count'], 1)


# ── Guest Votes (shared link) ────────────────────────────────────

class GuestVoteTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll, self.slot1, self.slot2 = self._make_poll_with_slots()

    def test_guest_vote_via_shared_link(self):
        resp = self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Alice',
                'guest_email': 'alice@ext.com',
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        vote = PollVote.objects.get(guest_name='Alice')
        self.assertEqual(vote.choice, 'yes')
        self.assertIsNone(vote.user)

    def test_guest_vote_returns_voter_token(self):
        resp = self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Bob',
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        self.assertIn('voter_token', resp.data)
        self.assertTrue(len(resp.data['voter_token']) > 0)
        # Cookie should be set
        self.assertIn(f'poll_voter_{self.poll.share_token}', resp.cookies)

    def test_guest_vote_generates_token_when_empty(self):
        resp = self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Carol',
                'voter_token': '',
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        vote = PollVote.objects.get(guest_name='Carol')
        self.assertTrue(len(vote.voter_token) > 0)

    def test_guest_vote_reuses_token(self):
        """Same voter_token updates existing votes instead of creating new ones."""
        token = 'test-token-12345'
        self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Dave',
                'voter_token': token,
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        # Vote again with same token, different choice
        self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Dave Updated',
                'voter_token': token,
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'no'}],
            },
            format='json',
        )
        votes = PollVote.objects.filter(voter_token=token)
        self.assertEqual(votes.count(), 1)
        self.assertEqual(votes.first().choice, 'no')
        self.assertEqual(votes.first().guest_name, 'Dave Updated')

    def test_guest_vote_restores_on_get(self):
        """GET shared link with voter_token returns previous votes."""
        token = 'restore-token-123'
        self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Eve',
                'guest_email': 'eve@test.com',
                'voter_token': token,
                'votes': [
                    {'slot_id': str(self.slot1.uuid), 'choice': 'yes'},
                    {'slot_id': str(self.slot2.uuid), 'choice': 'maybe'},
                ],
            },
            format='json',
        )
        resp = self.client.get(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}?voter_token={token}',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('my_votes', resp.data)
        self.assertEqual(resp.data['my_votes'][str(self.slot1.uuid)], 'yes')
        self.assertEqual(resp.data['my_votes'][str(self.slot2.uuid)], 'maybe')
        self.assertEqual(resp.data['my_guest_name'], 'Eve')
        self.assertEqual(resp.data['my_guest_email'], 'eve@test.com')

    def test_guest_vote_get_without_token_has_no_my_votes(self):
        resp = self.client.get(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('my_votes', resp.data)

    def test_shared_link_get(self):
        resp = self.client.get(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Test poll')

    def test_shared_link_invalid_token(self):
        resp = self.client.get('/api/v1/calendar/polls/shared/nonexistent')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_guest_cannot_vote_on_closed_poll(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        resp = self.client.post(
            f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote',
            {
                'guest_name': 'Late',
                'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_guest_vote_rate_limit(self):
        cache.clear()
        url = f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote'
        payload = {
            'guest_name': 'Spammer',
            'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
        }
        for i in range(10):
            resp = self.client.post(url, payload, format='json')
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # 11th should be rate limited
        resp = self.client.post(url, payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ── Finalize ─────────────────────────────────────────────────────

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
        PollSlot.objects.create(
            poll=self.poll,
            start=timezone.now() + timedelta(days=2),
            position=1,
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
        self.assertEqual(event.end, self.slot.end)
        self.assertTrue(
            EventMember.objects.filter(event=event, user=self.voter).exists()
        )

    def test_finalize_sets_chosen_slot(self):
        self.client.force_authenticate(user=self.owner)
        self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.chosen_slot, self.slot)

    def test_finalize_excludes_no_voters_from_event(self):
        """Users who voted 'no' are not added as event members."""
        no_voter = User.objects.create_user(username='novote', password='pass')
        PollVote.objects.create(slot=self.slot, user=no_voter, choice='no')
        self.client.force_authenticate(user=self.owner)
        self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.poll.refresh_from_db()
        self.assertFalse(
            EventMember.objects.filter(event=self.poll.event, user=no_voter).exists()
        )

    def test_finalize_includes_maybe_voters(self):
        maybe_voter = User.objects.create_user(username='maybe', password='pass')
        PollVote.objects.create(slot=self.slot, user=maybe_voter, choice='maybe')
        self.client.force_authenticate(user=self.owner)
        self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/finalize',
            {'slot_id': str(self.slot.uuid)},
            format='json',
        )
        self.poll.refresh_from_db()
        self.assertTrue(
            EventMember.objects.filter(event=self.poll.event, user=maybe_voter).exists()
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


# ── Invitations ──────────────────────────────────────────────────

class PollInviteTests(PollTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.poll, self.slot1, self.slot2 = self._make_poll_with_slots()

    def test_invite_user(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(PollInvitee.objects.filter(poll=self.poll, user=self.voter).exists())
        self.assertEqual(len(resp.data['invitees']), 1)

    def test_invite_excludes_creator(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.owner.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(PollInvitee.objects.filter(poll=self.poll, user=self.owner).exists())

    def test_invite_duplicate_is_idempotent(self):
        PollInvitee.objects.create(poll=self.poll, user=self.voter)
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(PollInvitee.objects.filter(poll=self.poll, user=self.voter).count(), 1)

    def test_only_creator_can_invite(self):
        self.client.force_authenticate(user=self.voter)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.owner.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_invite_to_closed_poll(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_remove_invitee(self):
        PollInvitee.objects.create(poll=self.poll, user=self.voter)
        self.client.force_authenticate(user=self.owner)
        resp = self.client.delete(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(PollInvitee.objects.filter(poll=self.poll, user=self.voter).exists())

    def test_remove_invitee_deletes_their_votes(self):
        PollInvitee.objects.create(poll=self.poll, user=self.voter)
        PollVote.objects.create(slot=self.slot1, user=self.voter, choice='yes')
        PollVote.objects.create(slot=self.slot2, user=self.voter, choice='maybe')
        self.assertEqual(PollVote.objects.filter(user=self.voter).count(), 2)

        self.client.force_authenticate(user=self.owner)
        self.client.delete(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        self.assertEqual(PollVote.objects.filter(user=self.voter).count(), 0)

    def test_remove_invitee_only_deletes_votes_for_that_poll(self):
        """Removing from poll A doesn't delete votes on poll B."""
        PollInvitee.objects.create(poll=self.poll, user=self.voter)
        PollVote.objects.create(slot=self.slot1, user=self.voter, choice='yes')

        other_poll, other_slot, _ = self._make_poll_with_slots(title='Other')
        PollVote.objects.create(slot=other_slot, user=self.voter, choice='yes')

        self.client.force_authenticate(user=self.owner)
        self.client.delete(
            f'/api/v1/calendar/polls/{self.poll.uuid}/invite',
            {'user_ids': [self.voter.id]},
            format='json',
        )
        # Vote on other poll should still exist
        self.assertTrue(PollVote.objects.filter(slot=other_slot, user=self.voter).exists())
        # Vote on this poll should be gone
        self.assertFalse(PollVote.objects.filter(slot=self.slot1, user=self.voter).exists())
