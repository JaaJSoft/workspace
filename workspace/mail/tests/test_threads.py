from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.threads import get_thread

User = get_user_model()


class GetThreadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='t', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='t@example.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )

    def _make(self, uid, message_id, in_reply_to=''):
        return MailMessage.objects.create(
            account=self.account, folder=self.folder,
            imap_uid=uid, message_id=message_id,
            in_reply_to=in_reply_to, date=timezone.now(),
        )

    def test_solo_message_returns_itself(self):
        m = self._make(1, '<a@x>')
        self.assertEqual(get_thread(m), [m])

    def test_two_message_chain_returned_in_order(self):
        parent = self._make(1, '<a@x>')
        child = self._make(2, '<b@x>', in_reply_to='<a@x>')
        self.assertEqual(get_thread(child), [parent, child])

    def test_three_level_chain(self):
        a = self._make(1, '<a@x>')
        b = self._make(2, '<b@x>', in_reply_to='<a@x>')
        c = self._make(3, '<c@x>', in_reply_to='<b@x>')
        self.assertEqual(get_thread(c), [a, b, c])

    def test_broken_chain_returns_what_we_have(self):
        c = self._make(3, '<c@x>', in_reply_to='<missing@x>')
        self.assertEqual(get_thread(c), [c])

    def test_max_depth_caps_walk(self):
        prev_id = ''
        msgs = []
        for i in range(30):
            mid = f'<m{i}@x>'
            m = self._make(i + 1, mid, in_reply_to=prev_id)
            msgs.append(m)
            prev_id = mid
        result = get_thread(msgs[-1], max_depth=5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[-1], msgs[-1])

    def test_walk_scoped_to_same_account(self):
        other_user = User.objects.create_user(username='u2', password='p')
        other_acc = MailAccount.objects.create(
            owner=other_user, email='u2@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        other_folder = MailFolder.objects.create(
            account=other_acc, name='INBOX', folder_type='inbox',
        )
        MailMessage.objects.create(
            account=other_acc, folder=other_folder, imap_uid=1,
            message_id='<a@x>', in_reply_to='', date=timezone.now(),
        )
        child = self._make(2, '<b@x>', in_reply_to='<a@x>')
        self.assertEqual(get_thread(child), [child])
