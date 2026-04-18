from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailLabel, MailMessage, MailMessageLabel

User = get_user_model()


class MailLabelCRUDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lblcrud', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='lbl@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='lbl@test.com',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_labels(self):
        resp = self.client.get(f'/api/v1/mail/labels?account={self.account.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 5)
        self.assertEqual(resp.data[0]['name'], 'Urgent')

    def test_list_requires_account(self):
        resp = self.client.get('/api/v1/mail/labels')
        self.assertEqual(resp.status_code, 400)

    def test_list_other_user_account(self):
        user2 = User.objects.create_user(username='other', password='pass')
        acc2 = MailAccount.objects.create(
            owner=user2, email='other@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='other@test.com',
        )
        resp = self.client.get(f'/api/v1/mail/labels?account={acc2.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_create_label(self):
        resp = self.client.post('/api/v1/mail/labels', {
            'account_id': str(self.account.uuid),
            'name': 'Custom',
            'color': 'accent',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], 'Custom')
        self.assertTrue(MailLabel.objects.filter(account=self.account, name='Custom').exists())

    def test_create_duplicate_name(self):
        resp = self.client.post('/api/v1/mail/labels', {
            'account_id': str(self.account.uuid),
            'name': 'Urgent',
        })
        self.assertEqual(resp.status_code, 400)

    def test_update_label(self):
        label = self.account.labels.first()
        resp = self.client.patch(
            f'/api/v1/mail/labels/{label.uuid}',
            {'color': 'primary', 'icon': 'star'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        label.refresh_from_db()
        self.assertEqual(label.color, 'primary')
        self.assertEqual(label.icon, 'star')

    def test_delete_label(self):
        label = self.account.labels.first()
        resp = self.client.delete(f'/api/v1/mail/labels/{label.uuid}')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MailLabel.objects.filter(pk=label.pk).exists())

    def test_delete_other_user_label(self):
        user2 = User.objects.create_user(username='other2', password='pass')
        acc2 = MailAccount.objects.create(
            owner=user2, email='other2@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='other2@test.com',
        )
        label2 = acc2.labels.first()
        resp = self.client.delete(f'/api/v1/mail/labels/{label2.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated(self):
        self.client.logout()
        resp = self.client.get(f'/api/v1/mail/labels?account={self.account.uuid}')
        self.assertIn(resp.status_code, [401, 403])


class MessageLabelFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='filteruser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='filter@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='filter@test.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.folder2 = MailFolder.objects.create(
            account=self.account, name='Archive',
            display_name='Archive', folder_type='archive',
        )
        self.msg1 = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1, subject='Urgent mail',
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=2, subject='Normal mail',
        )
        self.msg3 = MailMessage.objects.create(
            account=self.account, folder=self.folder2, imap_uid=1, subject='Archived urgent',
        )
        self.label_urgent = self.account.labels.get(name='Urgent')
        MailMessageLabel.objects.create(message=self.msg1, label=self.label_urgent)
        MailMessageLabel.objects.create(message=self.msg3, label=self.label_urgent)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_filter_by_label_cross_folder(self):
        resp = self.client.get(f'/api/v1/mail/messages?label={self.label_urgent.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 2)
        uuids = {m['uuid'] for m in resp.data['results']}
        self.assertIn(str(self.msg1.uuid), uuids)
        self.assertIn(str(self.msg3.uuid), uuids)

    def test_filter_by_label_and_folder(self):
        resp = self.client.get(
            f'/api/v1/mail/messages?folder={self.folder.uuid}&label={self.label_urgent.uuid}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(self.msg1.uuid))

    def test_label_without_folder_ok(self):
        resp = self.client.get(f'/api/v1/mail/messages?label={self.label_urgent.uuid}')
        self.assertEqual(resp.status_code, 200)

    def test_neither_folder_nor_label(self):
        resp = self.client.get('/api/v1/mail/messages')
        self.assertEqual(resp.status_code, 400)

    def test_message_includes_labels_field(self):
        resp = self.client.get(f'/api/v1/mail/messages?folder={self.folder.uuid}')
        self.assertEqual(resp.status_code, 200)
        msg_data = next(m for m in resp.data['results'] if m['uuid'] == str(self.msg1.uuid))
        self.assertEqual(len(msg_data['labels']), 1)
        self.assertEqual(msg_data['labels'][0]['name'], 'Urgent')
        self.assertEqual(msg_data['labels'][0]['color'], 'error')

    def test_other_user_label_rejected(self):
        user2 = User.objects.create_user(username='other3', password='pass')
        acc2 = MailAccount.objects.create(
            owner=user2, email='o3@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='o3@test.com',
        )
        label2 = acc2.labels.first()
        resp = self.client.get(f'/api/v1/mail/messages?label={label2.uuid}')
        self.assertEqual(resp.status_code, 404)


class MessageLabelAssignTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='assignuser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='assign@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='assign@test.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1, subject='Test',
        )
        self.label_urgent = self.account.labels.get(name='Urgent')
        self.label_fyi = self.account.labels.get(name='FYI')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_assign_labels(self):
        resp = self.client.post(
            f'/api/v1/mail/messages/{self.msg.uuid}/labels',
            {'label_ids': [str(self.label_urgent.uuid), str(self.label_fyi.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.msg.message_labels.count(), 2)

    def test_assign_idempotent(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label_urgent)
        resp = self.client.post(
            f'/api/v1/mail/messages/{self.msg.uuid}/labels',
            {'label_ids': [str(self.label_urgent.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.msg.message_labels.count(), 1)

    def test_remove_labels(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label_urgent)
        MailMessageLabel.objects.create(message=self.msg, label=self.label_fyi)
        resp = self.client.delete(
            f'/api/v1/mail/messages/{self.msg.uuid}/labels',
            {'label_ids': [str(self.label_urgent.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.msg.message_labels.count(), 1)
        self.assertEqual(self.msg.message_labels.first().label, self.label_fyi)

    def test_assign_other_account_label_rejected(self):
        user2 = User.objects.create_user(username='other4', password='pass')
        acc2 = MailAccount.objects.create(
            owner=user2, email='o4@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='o4@test.com',
        )
        foreign_label = acc2.labels.first()
        resp = self.client.post(
            f'/api/v1/mail/messages/{self.msg.uuid}/labels',
            {'label_ids': [str(foreign_label.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_assign_to_other_user_message(self):
        user2 = User.objects.create_user(username='other5', password='pass')
        acc2 = MailAccount.objects.create(
            owner=user2, email='o5@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='o5@test.com',
        )
        folder2 = MailFolder.objects.create(
            account=acc2, name='INBOX', display_name='Inbox', folder_type='inbox',
        )
        msg2 = MailMessage.objects.create(
            account=acc2, folder=folder2, imap_uid=1, subject='Foreign',
        )
        resp = self.client.post(
            f'/api/v1/mail/messages/{msg2.uuid}/labels',
            {'label_ids': [str(self.label_urgent.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)
