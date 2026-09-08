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

    def test_the_lobby_dialog_is_mounted_exactly_once(self):
        """Two elements carrying one x-ref fight over the key: Alpine's ref
        cleanup runs when the losing branch unmounts and deletes the entry the
        surviving one had just written, so openLobby() finds nothing to show.
        The page mounts the dialog; the stage only draws the buttons."""
        self.client.force_login(self.host)
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('x-ref="lobbyDialog"'), 1)

    def test_the_call_stage_draws_the_lobby_button_without_the_dialog(self):
        """Read off the partial itself, not a page that includes it: the page
        test above counts one dialog either way once the stage stops shipping
        its own copy, so the rule that keeps it that way belongs here."""
        from django.template.loader import render_to_string

        stage = render_to_string(
            "chat/ui/partials/call_stage.html",
            {"meeting_controls": True, "chat_toggle": True},
        )
        self.assertIn('@click="openLobby()"', stage)
        self.assertNotIn("lobbyDialog", stage)

    def test_signed_in_stranger_gets_the_guest_document_with_their_name(self):
        self.client.force_login(self.outsider)
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="meet-signed-in-data"', html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("You will join this meeting as a guest, under your account", html)
        self.assertNotIn("admitGuest(", html)

    def test_the_signed_in_knock_name_is_locked_and_the_anonymous_one_is_not(self):
        """The server derives a signed-in visitor's name from their account, so
        the field states it rather than inviting an edit the knock discards."""
        self.client.force_login(self.outsider)
        signed_in = self.client.get(self.url).content.decode()
        field = signed_in[signed_in.index('id="meet-display-name"') :][:400]
        self.assertIn("readonly", field)

        self.client.logout()
        anonymous = self.client.get(self.url).content.decode()
        field = anonymous[anonymous.index('id="meet-display-name"') :][:400]
        self.assertNotIn("readonly", field)

    def test_the_lobby_shows_a_bound_guest_as_their_own_account(self):
        """A signed-in visitor's knock binds their account, so the host admits
        someone the workspace can name: real avatar, workspace mark."""
        self.client.force_login(self.host)
        html = self.client.get(self.url).content.decode()
        self.assertIn(':user-id="g.user_id"', html)
        self.assertIn('x-show="g.user_id"', html)

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
