from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import EventMember
from workspace.chat.models import MeetingMessage
from workspace.chat.services import call_signaling as sig
from workspace.chat.services.meeting_messages import (
    MEETING_MESSAGE_MAX_LENGTH,
    delete_message,
    messages_for_guest,
    messages_for_host,
    post_message,
)
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting
from workspace.chat.services.participant_keys import guest_key, user_key

from .meeting_fixtures import guest_with_token, make_event

User = get_user_model()


class MeetingMessageServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user(username="mm-host", password="x")
        self.cohost = User.objects.create_user(username="mm-cohost", password="x")
        self.now = timezone.now()
        self.event = make_event(
            self.host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        EventMember.objects.create(event=self.event, user=self.cohost)
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.guest, self.token = guest_with_token(self.meeting, self.occurrence_start)

    def tearDown(self):
        cache.clear()

    def test_post_renders_and_stamps_the_occurrence(self):
        msg = post_message(
            self.meeting, "**hi** @everyone", author=self.host, now=self.now
        )
        self.assertEqual(msg.occurrence_start, self.occurrence_start)
        self.assertIn("<strong>hi</strong>", msg.body_html)
        self.assertNotIn("mention", msg.body_html)

    def test_post_fans_out_to_hosts_and_admitted_guests_but_not_the_author(self):
        post_message(self.meeting, "hello", author=self.host, now=self.now)
        self.assertEqual(sig.drain_events(user_key(self.host.id)), [])
        cohost = sig.drain_events(user_key(self.cohost.id))
        guest = sig.drain_events(guest_key(self.guest.uuid))
        self.assertEqual(cohost[0]["event"], "meeting_message")
        self.assertEqual(cohost[0]["data"]["message"]["body"], "hello")
        self.assertEqual(guest[0]["event"], "meeting_message")

    def test_a_guest_reads_only_its_occurrence(self):
        earlier = post_message(self.meeting, "old", author=self.host, now=self.now)
        MeetingMessage.objects.filter(pk=earlier.pk).update(
            occurrence_start=self.occurrence_start - timedelta(days=7)
        )
        post_message(self.meeting, "new", guest=self.guest, now=self.now)
        host_rows, _ = messages_for_host(self.meeting)
        guest_rows, _ = messages_for_guest(self.guest)
        self.assertEqual([m.body for m in host_rows], ["old", "new"])
        self.assertEqual([m.body for m in guest_rows], ["new"])

    def test_pagination_cursor(self):
        for i in range(3):
            post_message(self.meeting, f"m{i}", author=self.host, now=self.now)
        rows, has_more = messages_for_host(self.meeting, limit=2)
        self.assertTrue(has_more)
        self.assertEqual([m.body for m in rows], ["m1", "m2"])
        older, has_more = messages_for_host(self.meeting, before=rows[0].uuid, limit=2)
        self.assertFalse(has_more)
        self.assertEqual([m.body for m in older], ["m0"])

    def test_delete_fans_out(self):
        msg = post_message(self.meeting, "oops", guest=self.guest, now=self.now)
        msg_pk = msg.pk
        sig.drain_events(user_key(self.cohost.id))
        delete_message(msg)
        # delete() clears the instance's pk, so it is captured above.
        self.assertFalse(MeetingMessage.objects.filter(pk=msg_pk).exists())
        events = sig.drain_events(user_key(self.cohost.id))
        self.assertEqual(events[-1]["event"], "meeting_message_deleted")
        self.assertEqual(events[-1]["data"]["message_id"], str(msg_pk))


class MeetingMessageViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user(username="mv-host", password="x")
        self.outsider = User.objects.create_user(username="mv-out", password="x")
        self.now = timezone.now()
        self.event = make_event(
            self.host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.guest, self.token = guest_with_token(self.meeting, self.occurrence_start)
        self.client = APIClient()
        self.host_url = f"/api/v1/chat/meetings/{self.meeting.uuid}/messages"
        self.guest_url = f"/api/v1/chat/meet/{self.meeting.slug}/messages"

    def tearDown(self):
        cache.clear()

    def test_host_posts_and_lists(self):
        self.client.force_authenticate(self.host)
        resp = self.client.post(self.host_url, {"body": "hi"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author"]["id"], self.host.id)
        self.assertFalse(resp.data["author"]["is_guest"])
        listed = self.client.get(self.host_url)
        self.assertEqual([m["body"] for m in listed.data["messages"]], ["hi"])

    def test_outsider_is_404(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.host_url).status_code, 404)
        self.assertEqual(
            self.client.post(self.host_url, {"body": "x"}, format="json").status_code,
            404,
        )

    def test_guest_posts_and_lists_with_the_token(self):
        resp = self.client.post(
            self.guest_url,
            {"body": "from outside"},
            format="json",
            HTTP_X_MEETING_TOKEN=self.token,
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data["author"]["is_guest"])
        self.assertEqual(
            resp.data["author"]["participant_key"], guest_key(self.guest.uuid)
        )
        listed = self.client.get(self.guest_url, HTTP_X_MEETING_TOKEN=self.token)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data["messages"]), 1)

    def test_guest_without_a_token_is_404(self):
        self.assertEqual(self.client.get(self.guest_url).status_code, 404)
        self.assertEqual(
            self.client.post(self.guest_url, {"body": "x"}, format="json").status_code,
            404,
        )

    def test_blank_and_oversized_bodies_are_400(self):
        self.client.force_authenticate(self.host)
        for body in ("   ", "x" * (MEETING_MESSAGE_MAX_LENGTH + 1)):
            resp = self.client.post(self.host_url, {"body": body}, format="json")
            self.assertEqual(resp.status_code, 400)

    def test_host_deletes_any_message_guest_cannot(self):
        msg = post_message(self.meeting, "bye", guest=self.guest, now=self.now)
        self.client.force_authenticate(self.host)
        resp = self.client.delete(f"{self.host_url}/{msg.uuid}")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MeetingMessage.objects.filter(pk=msg.pk).exists())
        msg2 = post_message(self.meeting, "again", author=self.host, now=self.now)
        self.client.force_authenticate(None)
        resp = self.client.delete(
            f"{self.guest_url}/{msg2.uuid}", HTTP_X_MEETING_TOKEN=self.token
        )
        self.assertIn(resp.status_code, (404, 405))
