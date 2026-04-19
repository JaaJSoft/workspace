import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.passwords.models import Vault

User = get_user_model()


class PasswordsIndexViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='pass')
        self.client.login(username='testuser', password='pass')

    def test_vaults_json_contains_expected_fields(self):
        vault = Vault.objects.create(user=self.user, name='Personal')
        response = self.client.get(reverse('passwords_ui:index'))
        self.assertEqual(response.status_code, 200)
        vaults_json = json.loads(response.context['vaults_json'])
        self.assertEqual(len(vaults_json), 1)
        v = vaults_json[0]
        self.assertIn('is_favorite', v)
        self.assertIn('is_owner', v)
        self.assertIn('updated_at', v)
        self.assertTrue(v['is_owner'])
        self.assertFalse(v['is_favorite'])
        self.assertEqual(v['uuid'], str(vault.uuid))
