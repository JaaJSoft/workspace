"""Tests for search functions across all modules."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


# ── Chat search ─────────────────────────────────────────────────

class ChatSearchTests(TestCase):

    def setUp(self):
        from workspace.chat.models import Conversation, ConversationMember
        self.alice = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Smith',
        )
        self.bob = User.objects.create_user(
            username='bob', password='pass', first_name='Bob', last_name='Jones',
        )
        # Group conversation
        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='Engineering', created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=self.group, user=self.alice)
        # DM conversation
        self.dm = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=self.dm, user=self.alice)
        ConversationMember.objects.create(conversation=self.dm, user=self.bob)

    def test_search_group_by_title(self):
        from workspace.chat.search import search_conversations
        results = search_conversations('Engineer', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('Engineering', names)

    def test_search_dm_by_member_name(self):
        from workspace.chat.search import search_conversations
        results = search_conversations('Bob', self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_excludes_non_member_conversations(self):
        from workspace.chat.models import Conversation, ConversationMember
        from workspace.chat.search import search_conversations
        carol = User.objects.create_user(username='carol', password='pass')
        secret = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='Secret', created_by=carol,
        )
        ConversationMember.objects.create(conversation=secret, user=carol)
        results = search_conversations('Secret', self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_no_results_for_unmatched_query(self):
        from workspace.chat.search import search_conversations
        results = search_conversations('zzzznonexistent', self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Files search ────────────────────────────────────────────────

class FilesSearchTests(TestCase):

    def setUp(self):
        from workspace.files.models import File
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.f1 = File.objects.create(
            owner=self.alice, name='report.pdf', node_type=File.NodeType.FILE,
        )
        self.folder = File.objects.create(
            owner=self.alice, name='Documents', node_type=File.NodeType.FOLDER,
        )

    def test_search_by_filename(self):
        from workspace.files.search import search_files
        results = search_files('report', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('report.pdf', names)

    def test_search_finds_folders(self):
        from workspace.files.search import search_files
        results = search_files('Documents', self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_excludes_other_users_files(self):
        from workspace.files.models import File
        from workspace.files.search import search_files
        bob = User.objects.create_user(username='bob', password='pass')
        File.objects.create(owner=bob, name='secret.txt', node_type=File.NodeType.FILE)
        results = search_files('secret', self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_no_results_for_unmatched(self):
        from workspace.files.search import search_files
        results = search_files('zzzznonexistent', self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Notes search ────────────────────────────────────────────────

class NotesSearchTests(TestCase):

    def setUp(self):
        from workspace.files.models import File
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.note = File.objects.create(
            owner=self.alice, name='meeting-notes.md',
            node_type=File.NodeType.FILE, mime_type='text/markdown',
        )
        self.non_note = File.objects.create(
            owner=self.alice, name='meeting-notes.pdf',
            node_type=File.NodeType.FILE, mime_type='application/pdf',
        )

    def test_finds_markdown_files(self):
        from workspace.notes.search import search_notes
        results = search_notes('meeting', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('meeting-notes.md', names)

    def test_excludes_non_markdown(self):
        from workspace.notes.search import search_notes
        results = search_notes('meeting', self.alice, 10)
        names = [r.name for r in results]
        self.assertNotIn('meeting-notes.pdf', names)

    def test_no_results_for_unmatched(self):
        from workspace.notes.search import search_notes
        results = search_notes('zzzznonexistent', self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Calendar search ─────────────────────────────────────────────

class CalendarEventSearchTests(TestCase):

    def setUp(self):
        from workspace.calendar.models import Calendar, Event
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.cal = Calendar.objects.create(name='Work', owner=self.alice)
        self.event = Event.objects.create(
            calendar=self.cal, owner=self.alice,
            title='Sprint Planning', start=timezone.now(),
        )

    def test_search_by_title(self):
        from workspace.calendar.search import search_events
        results = search_events('Sprint', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('Sprint Planning', names)

    def test_excludes_cancelled(self):
        from workspace.calendar.models import Event
        from workspace.calendar.search import search_events
        self.event.is_cancelled = True
        self.event.save()
        results = search_events('Sprint', self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_other_users_events(self):
        from workspace.calendar.models import Calendar, Event
        from workspace.calendar.search import search_events
        bob = User.objects.create_user(username='bob', password='pass')
        bob_cal = Calendar.objects.create(name='Bob', owner=bob)
        Event.objects.create(
            calendar=bob_cal, owner=bob, title='Secret meeting', start=timezone.now(),
        )
        results = search_events('Secret', self.alice, 10)
        self.assertEqual(len(results), 0)


class CalendarPollSearchTests(TestCase):

    def setUp(self):
        from workspace.calendar.models import Poll
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.poll = Poll.objects.create(
            title='Team Lunch Date', created_by=self.alice,
        )

    def test_search_by_title(self):
        from workspace.calendar.search import search_polls
        results = search_polls('Lunch', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('Team Lunch Date', names)

    def test_excludes_other_users_polls(self):
        from workspace.calendar.search import search_polls
        bob = User.objects.create_user(username='bob', password='pass')
        results = search_polls('Lunch', bob, 10)
        self.assertEqual(len(results), 0)


# ── Mail search ─────────────────────────────────────────────────

class MailSearchTests(TestCase):

    def setUp(self):
        from workspace.mail.models import MailAccount, MailFolder, MailMessage
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.alice, email='alice@example.com',
            imap_host='imap.example.com', smtp_host='smtp.example.com',
            username='alice',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            subject='Invoice #1234', snippet='Please pay',
            from_address={'email': 'billing@corp.com', 'name': 'Billing'},
            date=timezone.now(),
        )

    def test_search_by_subject(self):
        from workspace.mail.search import search_mail
        results = search_mail('Invoice', self.alice, 10)
        names = [r.name for r in results]
        self.assertIn('Invoice #1234', names)

    def test_search_by_from_address(self):
        from workspace.mail.search import search_mail
        results = search_mail('billing', self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_excludes_other_users_mail(self):
        from workspace.mail.search import search_mail
        bob = User.objects.create_user(username='bob', password='pass')
        results = search_mail('Invoice', bob, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_hidden_folders(self):
        from workspace.mail.models import MailFolder, MailMessage
        from workspace.mail.search import search_mail
        hidden = MailFolder.objects.create(
            account=self.account, name='Hidden', display_name='Hidden',
            is_hidden=True,
        )
        MailMessage.objects.create(
            account=self.account, folder=hidden, imap_uid=2,
            subject='Hidden Invoice', date=timezone.now(),
        )
        results = search_mail('Hidden Invoice', self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_deleted_messages(self):
        from workspace.mail.search import search_mail
        self.msg.deleted_at = timezone.now()
        self.msg.save()
        results = search_mail('Invoice', self.alice, 10)
        self.assertEqual(len(results), 0)
