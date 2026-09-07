import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import EventMember
from workspace.chat.models import Meeting
from workspace.chat.services.meeting_hosts import (
    host_ids,
    hosted_meeting_ids,
    is_host,
    reachable_meeting,
)
from workspace.chat.services.meetings import create_ad_hoc_meeting, create_meeting

from .meeting_fixtures import make_event

User = get_user_model()


class HostDerivationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.pending = User.objects.create_user(username="pending", password="x")
        self.accepted = User.objects.create_user(username="accepted", password="x")
        self.declined = User.objects.create_user(username="declined", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        self.event = make_event(self.owner, start=timezone.now())
        EventMember.objects.create(event=self.event, user=self.pending)
        EventMember.objects.create(
            event=self.event, user=self.accepted, status=EventMember.Status.ACCEPTED
        )
        EventMember.objects.create(
            event=self.event, user=self.declined, status=EventMember.Status.DECLINED
        )
        self.meeting = create_meeting(self.event, self.owner)

    def test_owner_and_non_declined_invitees_host(self):
        self.assertEqual(
            host_ids(self.meeting),
            {self.owner.id, self.pending.id, self.accepted.id},
        )
        self.assertTrue(is_host(self.owner, self.meeting))
        self.assertTrue(is_host(self.pending, self.meeting))
        self.assertFalse(is_host(self.declined, self.meeting))
        self.assertFalse(is_host(self.stranger, self.meeting))

    def test_an_invitation_added_later_grants_hosting(self):
        EventMember.objects.create(event=self.event, user=self.stranger)
        self.assertTrue(is_host(self.stranger, self.meeting))

    def test_create_meeting_makes_no_conversation_and_copies_the_title(self):
        self.assertIsNone(self.meeting.conversation_id)
        self.assertEqual(self.meeting.title, self.event.title)

    def test_ad_hoc_meeting_is_hosted_by_its_creator_only(self):
        meeting = create_ad_hoc_meeting(self.stranger, "Quick sync")
        self.assertTrue(meeting.is_ad_hoc)
        self.assertEqual(host_ids(meeting), {self.stranger.id})
        self.assertFalse(is_host(self.owner, meeting))

    def test_hosted_meeting_ids_lists_both_kinds(self):
        ad_hoc = create_ad_hoc_meeting(self.pending, "Mine")
        self.assertEqual(
            set(hosted_meeting_ids(self.pending)), {self.meeting.uuid, ad_hoc.uuid}
        )
        self.assertEqual(set(hosted_meeting_ids(self.declined)), set())
        self.assertEqual(Meeting.objects.count(), 2)

    def test_reachable_meeting(self):
        self.assertEqual(reachable_meeting(self.owner, self.meeting.uuid), self.meeting)
        self.assertIsNone(reachable_meeting(self.declined, self.meeting.uuid))
        self.assertIsNone(reachable_meeting(self.owner, uuid.uuid4()))
