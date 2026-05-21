"""Tests for chat UI preferences (compact mode toggles).

The preferences themselves are persisted through the generic
``/api/v1/settings/<module>/<key>`` endpoint, which is exercised below to
confirm the chat module name and key are wired end-to-end. The chat index
page is also rendered to verify the preferences UI (script, popover,
dialog) is present.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.users.services.settings import get_setting, set_setting

User = get_user_model()


class ChatPreferencesEndpointTests(TestCase):
    SETTINGS_URL = '/api/v1/settings/chat/preferences'

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )

    def tearDown(self):
        cache.clear()

    def test_unauthenticated_is_forbidden(self):
        resp = self.client.get(self.SETTINGS_URL)
        self.assertIn(resp.status_code, (401, 403))

    def test_get_returns_404_when_no_preferences_stored(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.SETTINGS_URL)
        self.assertEqual(resp.status_code, 404)

    def test_put_stores_compact_flags_and_get_reads_them_back(self):
        self.client.force_login(self.user)
        payload = {
            'value': {
                'compactConversationList': True,
                'compactMessageView': False,
            },
        }
        put_resp = self.client.put(
            self.SETTINGS_URL, data=payload, content_type='application/json',
        )
        self.assertIn(put_resp.status_code, (200, 201))

        get_resp = self.client.get(self.SETTINGS_URL)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(
            get_resp.json()['value'],
            {'compactConversationList': True, 'compactMessageView': False},
        )

    def test_service_helpers_round_trip_chat_preferences(self):
        # Sanity-check that the cached helper layer used by the JS endpoint
        # writes back the same dict shape on read.
        set_setting(self.user, 'chat', 'preferences', {
            'compactConversationList': False,
            'compactMessageView': True,
        })
        value = get_setting(self.user, 'chat', 'preferences')
        self.assertEqual(value, {
            'compactConversationList': False,
            'compactMessageView': True,
        })


class ChatIndexPreferencesUITests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

    def tearDown(self):
        cache.clear()

    def test_chat_index_includes_preferences_script_and_dialog(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('chat_ui:index'))
        self.assertEqual(resp.status_code, 200)
        # The dedicated preferences JS file must be loaded.
        self.assertContains(resp, 'chat_preferences.js')
        # The mobile preferences dialog must exist.
        self.assertContains(resp, 'id="chat-prefs-dialog"')
        # Both compact toggles must be wired.
        self.assertContains(resp, 'compactConversationList')
        self.assertContains(resp, 'compactMessageView')
