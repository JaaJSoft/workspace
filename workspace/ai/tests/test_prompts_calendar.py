from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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

    @patch('workspace.ai.prompts.calendar.timezone.now')
    def test_user_message_includes_today_date(self, mock_now):
        """The prompt must include today's date so the LLM can resolve
        relative references ('tomorrow', 'next Tuesday') correctly."""
        mock_now.return_value = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        messages = build_event_extraction_messages([self._msg('Hi', 'body')])
        self.assertIn('2026-05-15', messages[1]['content'])

    @patch('workspace.ai.prompts.calendar.timezone.now')
    def test_user_message_renders_date_in_user_timezone(self, mock_now):
        """When a user_tz is passed, the 'today' rendering is the user's
        local date/time, not UTC. Critical to prevent the LLM from
        treating '8h' as UTC when the user is in Paris (+02:00)."""
        from zoneinfo import ZoneInfo
        # 22:30 UTC on May 15 is 00:30 May 16 in Paris (summer time UTC+2).
        mock_now.return_value = datetime(2026, 5, 15, 22, 30, tzinfo=timezone.utc)
        messages = build_event_extraction_messages(
            [self._msg('Hi', 'body')],
            user_tz=ZoneInfo('Europe/Paris'),
        )
        content = messages[1]['content']
        self.assertIn('2026-05-16 00:30', content)
        self.assertIn('Europe/Paris', content)
        self.assertIn('interpret it in Europe/Paris', content)
