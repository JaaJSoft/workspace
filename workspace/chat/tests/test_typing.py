"""Tests for workspace.chat.services.typing.

NOTE: patches replace the ``time`` module reference inside
``workspace.chat.services.typing`` — patching ``time.time`` globally would
also affect Django's cache backend, which uses it for TTL computation.
"""

from unittest import mock
from uuid import uuid4

from django.core.cache import cache
from django.test import SimpleTestCase

from workspace.chat.services import typing as typing_service
from workspace.chat.services.typing import (
    TYPING_STALE,
    TYPING_TTL,
    _cache_key,
    clear_typing,
    get_typing_users,
    set_typing,
)


def _fake_time(values):
    """Return a Mock replacement for typing_service.time."""
    fake = mock.Mock()
    if callable(values):
        fake.time.side_effect = values
    elif isinstance(values, (list, tuple)):
        fake.time.side_effect = list(values)
    else:
        fake.time.return_value = values
    return fake


class TypingServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.conv1 = uuid4()
        self.conv2 = uuid4()

    # ------------------------------------------------------------------
    # set_typing
    # ------------------------------------------------------------------

    def test_set_typing_stores_entry(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=42, display_name='Alice')

        raw = cache.get(_cache_key(self.conv1))
        self.assertIn('42', raw)
        self.assertEqual(raw['42']['display_name'], 'Alice')
        self.assertEqual(raw['42']['ts'], 1000.0)

    def test_set_typing_merges_multiple_users(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            set_typing(self.conv1, user_id=2, display_name='Bob')

        raw = cache.get(_cache_key(self.conv1))
        self.assertEqual(set(raw.keys()), {'1', '2'})

    def test_set_typing_overwrites_same_user(self):
        with mock.patch.object(
            typing_service, 'time', _fake_time([1000.0, 1005.0]),
        ):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            set_typing(self.conv1, user_id=1, display_name='Alice (renamed)')

        raw = cache.get(_cache_key(self.conv1))
        self.assertEqual(raw['1']['display_name'], 'Alice (renamed)')
        self.assertEqual(raw['1']['ts'], 1005.0)

    # ------------------------------------------------------------------
    # get_typing_users
    # ------------------------------------------------------------------

    def test_empty_conversation_list_returns_empty_dict(self):
        self.assertEqual(get_typing_users([]), {})

    def test_returns_fresh_entries(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            result = get_typing_users([self.conv1])

        self.assertEqual(list(result.keys()), [str(self.conv1)])
        self.assertEqual(result[str(self.conv1)], [
            {'user_id': '1', 'display_name': 'Alice'},
        ])

    def test_filters_stale_entries(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')

        # Advance the clock past TYPING_STALE.
        with mock.patch.object(
            typing_service, 'time', _fake_time(1000.0 + TYPING_STALE + 1),
        ):
            result = get_typing_users([self.conv1])

        self.assertEqual(result, {})

    def test_excludes_given_user(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            set_typing(self.conv1, user_id=2, display_name='Bob')
            result = get_typing_users([self.conv1], exclude_user_id=1)

        self.assertEqual(result[str(self.conv1)], [
            {'user_id': '2', 'display_name': 'Bob'},
        ])

    def test_spans_multiple_conversations(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            set_typing(self.conv2, user_id=2, display_name='Bob')
            result = get_typing_users([self.conv1, self.conv2])

        self.assertEqual(set(result.keys()), {str(self.conv1), str(self.conv2)})

    def test_conversation_with_only_excluded_user_is_omitted(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            result = get_typing_users([self.conv1], exclude_user_id=1)

        self.assertEqual(result, {})

    # ------------------------------------------------------------------
    # clear_typing
    # ------------------------------------------------------------------

    def test_clear_typing_removes_only_that_user(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')
            set_typing(self.conv1, user_id=2, display_name='Bob')

        clear_typing(self.conv1, user_id=1)
        raw = cache.get(_cache_key(self.conv1))
        self.assertEqual(list(raw.keys()), ['2'])

    def test_clear_typing_deletes_key_when_empty(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')

        clear_typing(self.conv1, user_id=1)
        self.assertIsNone(cache.get(_cache_key(self.conv1)))

    def test_clear_typing_noop_when_user_not_present(self):
        with mock.patch.object(typing_service, 'time', _fake_time(1000.0)):
            set_typing(self.conv1, user_id=1, display_name='Alice')

        clear_typing(self.conv1, user_id=999)
        raw = cache.get(_cache_key(self.conv1))
        self.assertIn('1', raw)

    def test_clear_typing_noop_on_empty_cache(self):
        # Must not raise when no key exists for the conversation.
        clear_typing(self.conv1, user_id=1)
        self.assertIsNone(cache.get(_cache_key(self.conv1)))

    # ------------------------------------------------------------------
    # Constants
    # ------------------------------------------------------------------

    def test_ttl_is_longer_than_stale(self):
        self.assertGreater(TYPING_TTL, TYPING_STALE)
