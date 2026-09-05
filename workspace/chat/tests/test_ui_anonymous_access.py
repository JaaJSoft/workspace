"""Every chat UI route except the public meeting page refuses an anonymous
visitor.

There is no LoginRequiredMiddleware: each view is gated on its own, so a
forgotten ``@login_required`` fails open and nothing else would notice. The
path list is therefore derived from ``chat/ui/urls.py`` itself rather than
hand-written, and a route whose captured arguments this module does not know
how to fill fails the test instead of being skipped.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.meetings import create_meeting
from workspace.chat.tests.meeting_fixtures import make_event
from workspace.chat.ui import urls as chat_ui_urls

User = get_user_model()


class ChatUiAnonymousAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", "u@example.com", "pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        event = make_event(
            self.user, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(event, self.user)

        # Every capture name any route in chat/ui/urls.py declares. The values
        # only have to be well-formed: the gate runs before the view body, so
        # a route that leaks would answer 200/404 on the object rather than
        # redirecting, and either way the assertion below catches it.
        self.arguments = {
            "conversation_uuid": self.conv.uuid,
            "message_uuid": uuid.uuid4(),
            "root_uuid": uuid.uuid4(),
            "attachment_uuid": uuid.uuid4(),
        }

    def _paths(self):
        paths = []
        for pattern in chat_ui_urls.urlpatterns:
            names = pattern.pattern.regex.groupindex.keys()
            unknown = set(names) - set(self.arguments)
            self.assertFalse(
                unknown,
                f"{pattern.name} captures {sorted(unknown)}: add a value for it "
                "so this route is actually covered by the fence",
            )
            kwargs = {name: self.arguments[name] for name in names}
            paths.append(
                reverse(f"{chat_ui_urls.app_name}:{pattern.name}", kwargs=kwargs)
            )
        return paths

    def test_every_chat_ui_route_refuses_anonymous(self):
        paths = self._paths()
        self.assertEqual(len(paths), len(chat_ui_urls.urlpatterns))
        for path in paths:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertIn(resp.status_code, (302, 401, 403), path)
                if resp.status_code == 302:
                    self.assertIn("/login", resp["Location"], path)

    def test_the_meeting_page_is_public(self):
        resp = self.client.get(f"/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
