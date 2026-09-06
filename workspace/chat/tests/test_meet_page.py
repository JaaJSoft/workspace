"""The public /meet/<slug> page: reachable by a stranger, and carrying
nothing about the meeting beyond its slug and the ICE configuration."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import ConversationMember
from workspace.chat.tests.meeting_fixtures import make_event
from workspace.chat.throttling import MeetingPublicPageThrottle

User = get_user_model()


class MeetPageTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        from workspace.chat.services.meetings import create_meeting

        self.meeting = create_meeting(self.event, self.host)

    def test_anonymous_visitor_gets_the_page(self):
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(f"chatMeetApp('{self.meeting.slug}')", html)
        self.assertIn('id="call-ice-servers-data"', html)
        # Standalone document: no app shell, nothing session-scoped.
        self.assertNotIn("workspace-modules-data", html)
        self.assertNotIn("/api/v1/stream", html)
        self.assertNotIn("service-worker", html)
        self.assertNotIn(self.host.email, html)
        self.assertNotIn(str(self.meeting.conversation_id), html)

    def test_page_carries_no_host_control_markup(self):
        """The guest page includes call_stage.html with host_controls=False,
        so none of the host-only Alpine bindings or the lobby card should
        reach the guest's HTML - a guest has no host endpoints to call and
        must not be shown controls that would 403."""
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        html = resp.content.decode()
        self.assertNotIn("admitGuest(", html)
        self.assertNotIn("toggleLock(", html)
        self.assertNotIn("endMeeting(", html)
        self.assertNotIn('id="room-meeting-data"', html)
        self.assertNotIn(">Lobby<", html)

    def test_unknown_slug_is_404(self):
        self.assertEqual(self.client.get("/meet/nope").status_code, 404)

    def test_guest_composer_has_no_attachments_voice_or_mentions(self):
        """The guest page includes _composer.html with a capability map that
        turns attachments, voice and mentions off, so none of the markup those
        three gates guard may reach the page: a guest has no attachment
        endpoints, no voice upload and no directory to mention against."""
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        html = resp.content.decode()
        # The composer itself is there - otherwise the assertions below pass
        # for the wrong reason.
        self.assertIn('x-ref="messageInput"', html)
        self.assertIn('placeholder="Type a message..."', html)
        # Attachments: drop zone, paste handler, chips and the attach menu.
        self.assertNotIn("handlePaste(", html)
        self.assertNotIn("handleDragEnter(", html)
        self.assertNotIn("handleDrop(", html)
        self.assertNotIn("openFileDialog()", html)
        self.assertNotIn("attachFromWorkspace()", html)
        self.assertNotIn('data-lucide="paperclip"', html)
        # Voice: the mic buttons, the recording banner and the preview player.
        self.assertNotIn("recorderState", html)
        self.assertNotIn("startRecording()", html)
        self.assertNotIn("chatAudioPlayer(", html)
        self.assertNotIn('data-lucide="mic"', html)
        # Mentions: the autocomplete dropdown and its @everyone row.
        self.assertNotIn("mentionActive", html)
        self.assertNotIn("insertMention(", html)
        self.assertNotIn("@everyone", html)


class MeetPageSignedInTests(TestCase):
    """What the link does for someone the workspace already knows.

    The page stays anonymous-capable, but a link handed around a room reaches
    members too, and sending a member through the guest lobby of their own
    meeting is a dead end: they would knock and wait for themselves.
    """

    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user("host2", "host2@example.com", "pw")
        self.outsider = User.objects.create_user(
            "outsider", "outsider@example.com", "pw"
        )
        self.outsider.first_name = "Ada"
        self.outsider.last_name = "Lovelace"
        self.outsider.save(update_fields=["first_name", "last_name"])
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        from workspace.chat.services.meetings import create_meeting

        self.meeting = create_meeting(self.event, self.host)

    def tearDown(self):
        cache.clear()

    def test_a_member_is_sent_to_the_room(self):
        self.client.force_login(self.host)
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"],
            reverse(
                "chat_ui:room",
                kwargs={"conversation_uuid": self.meeting.conversation_id},
            ),
        )

    def test_a_signed_in_stranger_gets_the_guest_page_with_their_name(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="meet-signed-in-data"', html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("You will join this meeting as a guest.", html)
        # Still the guest page: no host controls, no conversation id.
        self.assertNotIn("admitGuest(", html)
        self.assertNotIn('id="room-meeting-data"', html)
        self.assertNotIn(str(self.meeting.conversation_id), html)

    def test_the_note_claims_no_identity_the_knock_will_not_record(self):
        """The name is a prefill, not a credential: the knock endpoint is
        anonymous, so a host reading the lobby cannot tell a signed-in
        colleague from a stranger who typed the same name. Saying "signed in
        as X" next to that would promise a binding nothing records."""
        self.client.force_login(self.outsider)
        html = self.client.get(f"/meet/{self.meeting.slug}").content.decode()
        self.assertNotIn("Signed in as", html)

    def test_a_member_who_left_gets_the_guest_card(self):
        """Membership is the live one, not "was a member once": someone who
        left the conversation has no room to be sent to."""
        membership = ConversationMember.objects.get(
            conversation_id=self.meeting.conversation_id, user=self.host
        )
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])

        self.client.force_login(self.host)
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("You will join this meeting as a guest.", html)
        self.assertNotIn(str(self.meeting.conversation_id), html)

    def test_an_anonymous_visitor_is_offered_a_sign_in_link(self):
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        html = resp.content.decode()
        self.assertIn(f"/login?next=/meet/{self.meeting.slug}", html)
        self.assertNotIn("You will join this meeting as a guest.", html)

    def _spend_the_ip_bucket(self, ip):
        """Two anonymous page loads, which is the whole patched budget."""
        anonymous = Client()
        for _ in range(2):
            self.assertEqual(
                anonymous.get(f"/meet/{self.meeting.slug}", REMOTE_ADDR=ip).status_code,
                200,
            )
        return anonymous

    def test_the_anonymous_path_is_still_throttled_per_ip(self):
        with patch.object(MeetingPublicPageThrottle, "get_rate", return_value="2/min"):
            anonymous = self._spend_the_ip_bucket("198.51.100.7")
            blocked = anonymous.get(
                f"/meet/{self.meeting.slug}", REMOTE_ADDR="198.51.100.7"
            )
        self.assertEqual(blocked.status_code, 429)

    def test_guests_behind_one_ip_cannot_lock_a_member_out_of_their_room(self):
        """The bucket is per IP and sized for the anonymous surface, which
        refetches the message list on every event. A meeting's guests all sit
        behind one office NAT with the host, so spending that budget must not
        cost the host the room they are hosting - a signed-in caller is
        accountable by session, and the room view checks membership itself."""
        with patch.object(MeetingPublicPageThrottle, "get_rate", return_value="2/min"):
            self._spend_the_ip_bucket("198.51.100.8")
            self.client.force_login(self.host)
            resp = self.client.get(
                f"/meet/{self.meeting.slug}", REMOTE_ADDR="198.51.100.8"
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/chat/room/", resp["Location"])
