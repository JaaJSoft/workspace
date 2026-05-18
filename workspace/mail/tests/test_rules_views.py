import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage, MailRule

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='vu', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='v@x.com',
            imap_host='x', smtp_host='x', username='v@x.com',
        )
        self.label = self.account.labels.first()
        self.client = APIClient()
        self.client.force_authenticate(self.user)


class MailRuleCRUDTests(_Base):
    def test_list_empty(self):
        resp = self.client.get(f'/api/v1/mail/rules?account={self.account.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_list_other_user_account_404(self):
        other = User.objects.create_user(username='o', password='p')
        other_acc = MailAccount.objects.create(
            owner=other, email='o@x.com',
            imap_host='x', smtp_host='x', username='o@x.com',
        )
        resp = self.client.get(f'/api/v1/mail/rules?account={other_acc.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_list_malformed_account_400(self):
        resp = self.client.get('/api/v1/mail/rules?account=not-a-uuid')
        self.assertEqual(resp.status_code, 400)

    def test_create_rule(self):
        resp = self.client.post('/api/v1/mail/rules', {
            'account_id': str(self.account.uuid),
            'name': 'r1',
            'conditions': {'field': 'from', 'op': 'contains', 'value': '@news.com'},
            'actions': [{'type': 'add_label', 'label_id': str(self.label.uuid)}],
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['name'], 'r1')
        self.assertTrue(MailRule.objects.filter(account=self.account, name='r1').exists())

    def test_create_invalid_conditions_400(self):
        resp = self.client.post('/api/v1/mail/rules', {
            'account_id': str(self.account.uuid),
            'name': 'r1',
            'conditions': {'field': 'BOGUS', 'op': 'contains', 'value': 'x'},
            'actions': [{'type': 'mark_read'}],
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_no_actions_400(self):
        resp = self.client.post('/api/v1/mail/rules', {
            'account_id': str(self.account.uuid),
            'name': 'r1',
            'conditions': {'field': 'from', 'op': 'contains', 'value': 'x'},
            'actions': [],
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_get_detail(self):
        rule = MailRule.objects.create(account=self.account, name='r')
        resp = self.client.get(f'/api/v1/mail/rules/{rule.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['name'], 'r')

    def test_get_other_user_rule_404(self):
        other = User.objects.create_user(username='oo', password='p')
        other_acc = MailAccount.objects.create(
            owner=other, email='oo@x.com',
            imap_host='x', smtp_host='x', username='oo@x.com',
        )
        rule = MailRule.objects.create(account=other_acc, name='leak')
        resp = self.client.get(f'/api/v1/mail/rules/{rule.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_patch_rule(self):
        rule = MailRule.objects.create(account=self.account, name='old')
        resp = self.client.patch(
            f'/api/v1/mail/rules/{rule.uuid}',
            {'name': 'new', 'is_enabled': False},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.name, 'new')
        self.assertFalse(rule.is_enabled)

    def test_delete_rule(self):
        rule = MailRule.objects.create(account=self.account, name='r')
        resp = self.client.delete(f'/api/v1/mail/rules/{rule.uuid}')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MailRule.objects.filter(pk=rule.pk).exists())
