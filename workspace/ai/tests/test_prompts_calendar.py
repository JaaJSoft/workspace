from datetime import datetime, timezone
from unittest.mock import MagicMock

from django.test import TestCase

from workspace.ai.prompts.calendar import build_event_extraction_messages


class BuildEventExtractionMessagesTests(TestCase):
    def _msg(self, subject, body, frm='alice@x.com'):
        m = MagicMock()
        m.subject = subject
        m.body_text = body
        m.body_html = ''
        m.from_address = {'name': '', 'email': frm}
        m.date = datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc)
        return m

    def test_returns_system_and_user_messages(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'Meet at 3pm tomorrow.')])
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')

    def test_system_message_rejects_vague_proposals(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'x')])
        system = messages[0]['content'].lower()
        self.assertIn('confirmed', system)
        self.assertIn('reject', system)

    def test_user_message_includes_thread_in_chronological_order(self):
        m1 = self._msg('Plan', 'On se voit ?')
        m2 = self._msg('Re: Plan', 'Oui mardi 14h')
        messages = build_event_extraction_messages([m1, m2])
        content = messages[1]['content']
        self.assertLess(content.index('On se voit'), content.index('Oui mardi'))

    def test_user_message_wraps_body_in_untrusted_tags(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'body')])
        self.assertIn('<untrusted-content>', messages[1]['content'])
        self.assertIn('</untrusted-content>', messages[1]['content'])

    def test_long_bodies_are_truncated(self):
        long_body = 'x' * 10000
        messages = build_event_extraction_messages([self._msg('Hi', long_body)])
        self.assertLess(len(messages[1]['content']), 8000)
