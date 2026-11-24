"""The call room hosts the same conversation pane as the chat page, so it
binds every event that pane reacts to: an edit, a deletion, a reaction or a
pin pushed while the room is open shows up in its chat as well."""

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates" / "chat" / "ui"
BINDING = re.compile(r"@((?:chat|ai-stream)-[a-z_-]+)\.window=\"([^\"]+)\"")

# What the chat page binds for its own furniture: the conversation list
# (unread counts, renames) and the banner offering to join a call.
CHAT_PAGE_ONLY = {"chat-unread", "chat-conversation_updated", "chat-call_started"}


def _bindings(name):
    return dict(BINDING.findall((TEMPLATES / name).read_text(encoding="utf-8")))


class RoomPaneEventsTests(SimpleTestCase):
    def test_the_room_binds_every_event_of_the_pane(self):
        page = _bindings("index.html")
        room = _bindings("room.html")

        missing = {
            event: handler
            for event, handler in page.items()
            if event not in CHAT_PAGE_ONLY and room.get(event) != handler
        }
        self.assertEqual(missing, {})
