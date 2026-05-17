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
    def test_user_message_includes_message_date_as_anchor(self, mock_now):
        """The prompt must include the email's date (not today's date)
        as the anchor for relative references ('tomorrow', 'next
        Tuesday'), so an old email is interpreted relative to when it
        was sent."""
        mock_now.return_value = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        # _msg date is 2026-05-14 09:00 UTC.
        messages = build_event_extraction_messages([self._msg('Hi', 'body')])
        self.assertIn('2026-05-14', messages[1]['content'])

    @patch('workspace.ai.prompts.calendar.timezone.now')
    def test_user_message_renders_date_in_user_timezone(self, mock_now):
        """When a user_tz is passed, the anchor is rendered in the
        user's local date/time, not UTC. Critical to prevent the LLM
        from treating '8h' as UTC when the user is in Paris (+02:00)."""
        from zoneinfo import ZoneInfo
        mock_now.return_value = datetime(2026, 5, 15, 22, 30, tzinfo=timezone.utc)
        # _msg date is 2026-05-14 09:00 UTC == 11:00 Paris (summer UTC+2).
        messages = build_event_extraction_messages(
            [self._msg('Hi', 'body')],
            user_tz=ZoneInfo('Europe/Paris'),
        )
        content = messages[1]['content']
        self.assertIn('2026-05-14 11:00', content)
        self.assertIn('Europe/Paris', content)
        self.assertIn('interpret it in Europe/Paris', content)

    @patch('workspace.ai.prompts.calendar.timezone.now')
    def test_relative_time_anchors_on_most_recent_message_date(self, mock_now):
        """Regression: an old email saying 'demain a 8h' must be anchored
        on the email's date, not today's date. Without this, the LLM
        resolves 'demain' relative to now() and produces wrong dates
        (e.g. a March email opened in May would book the event for
        May+1 instead of March+1)."""
        from zoneinfo import ZoneInfo
        # Today is May 17 but the email was sent on March 1.
        mock_now.return_value = datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc)
        msg = MagicMock()
        msg.subject = 'Confirmation RDV'
        msg.body_text = 'On se voit demain a 8h'
        msg.body_html = ''
        msg.from_address = {'name': '', 'email': 'alice@x.com'}
        msg.date = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)

        messages = build_event_extraction_messages(
            [msg], user_tz=ZoneInfo('Europe/Paris')
        )
        content = messages[1]['content']
        # The anchor in the prompt must be the email's date...
        self.assertIn('2026-03-01', content)
        # ...and critically NOT today's date.
        self.assertNotIn('2026-05-17', content)

    @patch('workspace.ai.prompts.calendar.timezone.now')
    def test_anchor_uses_last_message_date_in_multi_message_thread(self, mock_now):
        """In a multi-message thread, the anchor must be the MOST RECENT
        message's date (the one likely containing the relative reference),
        not the first message's date and not today."""
        mock_now.return_value = datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc)
        m1 = self._msg('Plan', 'On se parle bientot ?')
        m1.date = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
        m2 = self._msg('Re: Plan', 'Ok demain 8h ?')
        m2.date = datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc)
        messages = build_event_extraction_messages([m1, m2])
        content = messages[1]['content']
        self.assertIn('2026-04-20', content)
        self.assertNotIn('2026-05-17', content)

    def test_anchor_falls_back_to_now_when_no_message_has_date(self):
        """If no message in the thread carries a date header, fall back
        to now() instead of crashing."""
        msg = self._msg('Hi', 'body')
        msg.date = None
        messages = build_event_extraction_messages([msg])
        self.assertEqual(messages[1]['role'], 'user')
