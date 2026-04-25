from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.activity import MailActivityProvider
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class MailActivityProviderTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.other_user = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        self.account = MailAccount.objects.create(
            owner=self.user,
            email='alice@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='alice@example.com',
        )
        self.other_account = MailAccount.objects.create(
            owner=self.other_user,
            email='bob@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='bob@example.com',
        )

        self.inbox = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.sent = MailFolder.objects.create(
            account=self.account, name='Sent',
            display_name='Sent', folder_type='sent',
        )
        self.other_inbox = MailFolder.objects.create(
            account=self.other_account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.other_sent = MailFolder.objects.create(
            account=self.other_account, name='Sent',
            display_name='Sent', folder_type='sent',
        )

        self.ts = timezone.now()

        # alice: 2 received, 1 sent
        MailMessage.objects.create(
            account=self.account, folder=self.inbox, imap_uid=1,
            subject='Received 1', date=self.ts,
            from_address={'name': 'External', 'address': 'ext@example.com'},
        )
        MailMessage.objects.create(
            account=self.account, folder=self.inbox, imap_uid=2,
            subject='Received 2', date=self.ts,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.sent, imap_uid=3,
            subject='Sent by Alice', date=self.ts,
        )

        # bob: 1 received, 2 sent
        MailMessage.objects.create(
            account=self.other_account, folder=self.other_inbox, imap_uid=1,
            subject='Received by Bob', date=self.ts,
        )
        MailMessage.objects.create(
            account=self.other_account, folder=self.other_sent, imap_uid=2,
            subject='Sent by Bob 1', date=self.ts,
        )
        MailMessage.objects.create(
            account=self.other_account, folder=self.other_sent, imap_uid=3,
            subject='Sent by Bob 2', date=self.ts,
        )

        self.provider = MailActivityProvider()

    # ── get_daily_counts ──────────────────────────────────

    def test_daily_counts_with_user_counts_only_sent(self):
        """Profile/heatmap: only sent mails count as user activity."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.user.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 1)  # only alice's 1 sent

    def test_daily_counts_with_user_counts_other_user(self):
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.other_user.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)  # bob's 2 sent

    def test_daily_counts_without_user_counts_only_inbox(self):
        """Dashboard/workspace: only received (inbox) mails."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            None, today, today,
        )
        # 2 inbox alice + 1 inbox bob = 3
        self.assertEqual(counts.get(today, 0), 3)

    # ── get_recent_events ─────────────────────────────────

    def test_recent_events_with_user_returns_sent_only(self):
        """Profile feed: only sent mails for that user."""
        events = self.provider.get_recent_events(self.user.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['label'], 'Email sent')
        self.assertEqual(events[0]['description'], 'Sent by Alice')

    def test_recent_events_without_user_returns_inbox_only(self):
        """Dashboard feed: only inbox mails across all users."""
        events = self.provider.get_recent_events(None)
        self.assertEqual(len(events), 3)
        for e in events:
            self.assertEqual(e['label'], 'Email received')

    def test_recent_events_sent_actor_uses_owner_name(self):
        """Sent mail actor should be the account owner."""
        events = self.provider.get_recent_events(self.user.id)
        self.assertEqual(events[0]['actor']['id'], self.user.id)
        self.assertEqual(events[0]['actor']['username'], 'alice')

    def test_recent_events_received_actor_uses_from_address(self):
        """Received mail actor should use the from_address when available."""
        events = self.provider.get_recent_events(None)
        received_1 = next(e for e in events if e['description'] == 'Received 1')
        self.assertEqual(received_1['actor']['full_name'], 'External')

    def test_recent_events_respects_limit_and_offset(self):
        events = self.provider.get_recent_events(None, limit=2, offset=0)
        self.assertEqual(len(events), 2)
        events = self.provider.get_recent_events(None, limit=2, offset=2)
        self.assertEqual(len(events), 1)

    # ── get_stats ─────────────────────────────────────────

    def test_stats_with_user_counts_all_messages(self):
        """Stats should count all messages (sent + received), not filtered."""
        stats = self.provider.get_stats(self.user.id)
        self.assertEqual(stats['total_messages'], 3)  # 2 inbox + 1 sent

    def test_stats_without_user_counts_all(self):
        stats = self.provider.get_stats(None)
        self.assertEqual(stats['total_messages'], 6)  # all messages

    # ── deleted messages excluded ─────────────────────────

    def test_deleted_messages_excluded_from_counts(self):
        MailMessage.objects.create(
            account=self.account, folder=self.sent, imap_uid=99,
            subject='Deleted sent', date=self.ts,
            deleted_at=timezone.now(),
        )
        today = self.ts.date()
        counts = self.provider.get_daily_counts(self.user.id, today, today)
        self.assertEqual(counts.get(today, 0), 1)  # still 1, deleted excluded

    def test_deleted_messages_excluded_from_events(self):
        MailMessage.objects.create(
            account=self.account, folder=self.sent, imap_uid=99,
            subject='Deleted sent', date=self.ts,
            deleted_at=timezone.now(),
        )
        events = self.provider.get_recent_events(self.user.id)
        self.assertEqual(len(events), 1)
