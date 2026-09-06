"""The public /meet/<slug> page: reachable by a stranger, and carrying
nothing about the meeting beyond its slug and the ICE configuration."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.tests.meeting_fixtures import make_event

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
