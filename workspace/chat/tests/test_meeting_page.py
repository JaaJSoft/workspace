from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from workspace.calendar.models import EventMember
from workspace.chat.services.meetings import create_meeting
from workspace.chat.tests.meeting_fixtures import make_event
from workspace.chat.throttling import MeetingPublicPageThrottle

User = get_user_model()


class MeetingPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.invitee = User.objects.create_user("invitee", "inv@example.com", "pw")
        self.outsider = User.objects.create_user("outsider", "out@example.com", "pw")
        self.outsider.first_name, self.outsider.last_name = "Ada", "Lovelace"
        self.outsider.save(update_fields=["first_name", "last_name"])
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        EventMember.objects.create(event=self.event, user=self.invitee)
        self.meeting = create_meeting(self.event, self.host)
        self.url = f"/meetings/{self.meeting.slug}"

    def tearDown(self):
        cache.clear()

    def test_anonymous_visitor_gets_the_guest_document(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(f"chatMeetApp('{self.meeting.slug}')", html)
        self.assertIn('id="call-ice-servers-data"', html)
        self.assertNotIn("workspace-modules-data", html)
        self.assertNotIn("/api/v1/stream", html)
        self.assertNotIn(self.host.email, html)
        self.assertNotIn(str(self.meeting.uuid), html)
        self.assertNotIn("admitGuest(", html)
        self.assertIn(f"/login?next=/meetings/{self.meeting.slug}", html)

    def test_host_and_invitee_get_the_host_page(self):
        for user in (self.host, self.invitee):
            self.client.force_login(user)
            resp = self.client.get(self.url)
            self.assertEqual(resp.status_code, 200, user)
            html = resp.content.decode()
            self.assertIn("chatMeetingHostApp(", html)
            self.assertIn('id="meeting-data"', html)
            self.assertIn("admitGuest(", html)
            self.assertIn("workspace-modules-data", html)

    def test_signed_in_stranger_gets_the_guest_document_with_their_name(self):
        self.client.force_login(self.outsider)
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="meet-signed-in-data"', html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("You will join this meeting as a guest.", html)
        self.assertNotIn("admitGuest(", html)

    def test_public_link_off_hides_the_page_from_non_hosts(self):
        self.meeting.public_link_enabled = False
        self.meeting.save(update_fields=["public_link_enabled"])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.host)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_unknown_slug_is_404(self):
        self.assertEqual(self.client.get("/meetings/nope").status_code, 404)

    def test_the_old_link_redirects(self):
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], self.url)

    def test_the_anonymous_path_is_throttled_per_ip_and_hosts_bypass_it(self):
        with patch.object(MeetingPublicPageThrottle, "get_rate", return_value="2/min"):
            anonymous = Client()
            for _ in range(2):
                self.assertEqual(
                    anonymous.get(self.url, REMOTE_ADDR="198.51.100.7").status_code, 200
                )
            self.assertEqual(
                anonymous.get(self.url, REMOTE_ADDR="198.51.100.7").status_code, 429
            )
            self.client.force_login(self.host)
            self.assertEqual(
                self.client.get(self.url, REMOTE_ADDR="198.51.100.7").status_code, 200
            )

    def test_the_room_page_carries_no_meeting_data_any_more(self):
        from workspace.chat.models import Conversation, ConversationMember

        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.host
        )
        ConversationMember.objects.create(conversation=conv, user=self.host)
        self.client.force_login(self.host)
        html = self.client.get(f"/chat/room/{conv.uuid}").content.decode()
        self.assertNotIn("room-meeting-data", html)
        self.assertNotIn("admitGuest(", html)
