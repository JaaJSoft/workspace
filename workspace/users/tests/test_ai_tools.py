"""Tests for workspace.users.ai_tools.UsersToolProvider.

These tests exercise the real orchestration logic (User lookup, status
formatting, bot filtering, output strings). Only the presence_service
functions are patched so the tests don't depend on a running cache or
background activity.
"""

import json
from datetime import datetime, timezone as dt_tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.users.ai_tools import (
    CheckUserStatusParams,
    ListOnlineUsersParams,
    UsersToolProvider,
)

User = get_user_model()


class CheckUserStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Smith',
        )
        cls.bob = User.objects.create_user(username='bob', password='pass')
        cls.offline_user = User.objects.create_user(
            username='carol', password='pass', first_name='Carol',
        )
        cls.inactive = User.objects.create_user(
            username='ghost', password='pass', is_active=False,
        )

    def _call(self, username):
        provider = UsersToolProvider()
        args = CheckUserStatusParams(username=username)
        return provider.check_user_status(
            args, user=self.alice, bot=None,
            conversation_id=None, context={},
        )

    def test_online_user_reports_full_name(self):
        with mock.patch(
            'workspace.users.services.presence.get_status', return_value='online',
        ), mock.patch(
            'workspace.users.services.presence.get_last_seen', return_value=None,
        ):
            result = self._call('alice')

        payload = json.loads(result)
        self.assertEqual(payload['username'], 'alice')
        self.assertEqual(payload['display_name'], 'Alice Smith')
        self.assertEqual(payload['status'], 'online')
        self.assertNotIn('last_seen', payload)

    def test_display_name_falls_back_to_username(self):
        # Bob has no first/last name.
        with mock.patch(
            'workspace.users.services.presence.get_status', return_value='away',
        ), mock.patch(
            'workspace.users.services.presence.get_last_seen', return_value=None,
        ):
            result = self._call('bob')

        payload = json.loads(result)
        self.assertEqual(payload['display_name'], 'bob')

    def test_offline_user_includes_last_seen(self):
        last_seen = datetime(2026, 4, 10, 14, 30, tzinfo=dt_tz.utc)
        with mock.patch(
            'workspace.users.services.presence.get_status', return_value='offline',
        ), mock.patch(
            'workspace.users.services.presence.get_last_seen', return_value=last_seen,
        ):
            result = self._call('carol')

        payload = json.loads(result)
        self.assertEqual(payload['status'], 'offline')
        self.assertEqual(payload['last_seen'], '2026-04-10 14:30')

    def test_offline_user_without_last_seen_omits_field(self):
        with mock.patch(
            'workspace.users.services.presence.get_status', return_value='offline',
        ), mock.patch(
            'workspace.users.services.presence.get_last_seen', return_value=None,
        ):
            result = self._call('carol')

        payload = json.loads(result)
        self.assertNotIn('last_seen', payload)

    def test_blank_username_is_rejected(self):
        # Pydantic allows the blank string, so the method itself guards it.
        result = self._call('   ')
        self.assertEqual(result, 'Error: username is required')

    def test_case_insensitive_lookup(self):
        with mock.patch(
            'workspace.users.services.presence.get_status', return_value='online',
        ), mock.patch(
            'workspace.users.services.presence.get_last_seen', return_value=None,
        ):
            result = self._call('ALICE')

        payload = json.loads(result)
        self.assertEqual(payload['username'], 'alice')

    def test_inactive_user_is_treated_as_not_found(self):
        result = self._call('ghost')
        self.assertEqual(result, 'User "ghost" not found.')

    def test_unknown_user_returns_message(self):
        result = self._call('nobody')
        self.assertEqual(result, 'User "nobody" not found.')


class ListOnlineUsersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.caller = User.objects.create_user(username='caller', password='pass')
        cls.alice = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Smith',
        )
        cls.bob = User.objects.create_user(username='bob', password='pass')
        cls.bot_user = User.objects.create_user(username='botty', password='pass')

        # Minimal BotProfile so "botty" is excluded from the list.
        from workspace.ai.models import BotProfile
        BotProfile.objects.create(user=cls.bot_user)

    def _call(self, limit=20):
        provider = UsersToolProvider()
        args = ListOnlineUsersParams(limit=limit)
        return provider.list_online_users(
            args, user=self.caller, bot=None,
            conversation_id=None, context={},
        )

    def test_empty_when_no_online_users(self):
        with mock.patch(
            'workspace.users.services.presence.get_online_user_ids',
            return_value=[],
        ):
            result = self._call()
        self.assertEqual(result, 'No users are currently online.')

    def test_returns_online_users_with_status(self):
        with mock.patch(
            'workspace.users.services.presence.get_online_user_ids',
            return_value=[self.alice.id, self.bob.id],
        ), mock.patch(
            'workspace.users.services.presence.get_statuses',
            return_value={self.alice.id: 'online', self.bob.id: 'away'},
        ):
            result = self._call()

        self.assertIn('Alice Smith (@alice) — online', result)
        self.assertIn('bob (@bob) — away', result)

    def test_filters_out_bots(self):
        with mock.patch(
            'workspace.users.services.presence.get_online_user_ids',
            return_value=[self.alice.id, self.bot_user.id],
        ), mock.patch(
            'workspace.users.services.presence.get_statuses',
            return_value={self.alice.id: 'online', self.bot_user.id: 'online'},
        ):
            result = self._call()

        self.assertIn('@alice', result)
        self.assertNotIn('botty', result)

    def test_message_when_only_bots_are_online(self):
        with mock.patch(
            'workspace.users.services.presence.get_online_user_ids',
            return_value=[self.bot_user.id],
        ), mock.patch(
            'workspace.users.services.presence.get_statuses',
            return_value={self.bot_user.id: 'online'},
        ):
            result = self._call()

        self.assertEqual(result, 'No users are currently online.')

    def test_limit_is_capped_at_50(self):
        # Even if the caller asks for 9999, the query only fetches 50.
        with mock.patch(
            'workspace.users.services.presence.get_online_user_ids',
            return_value=[self.alice.id],
        ), mock.patch(
            'workspace.users.services.presence.get_statuses',
            return_value={self.alice.id: 'online'},
        ), mock.patch(
            'django.contrib.auth.get_user_model', return_value=User,
        ):
            result = self._call(limit=9999)

        self.assertIn('@alice', result)
