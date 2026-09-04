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
