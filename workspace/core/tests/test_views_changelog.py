from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase

from workspace.core import changelog as changelog_module
from workspace.core.changelog import parse_version
from workspace.core.context_processors import workspace_modules
from workspace.core.views_changelog import (
    CHANGELOG_SETTING_KEY,
    CHANGELOG_SETTING_MODULE,
)
from workspace.users.services.settings import get_setting, set_setting

User = get_user_model()


class ParseVersionTests(TestCase):
    def test_three_part_numeric_version(self):
        self.assertEqual(parse_version('0.20.0'), (0, 20, 0))

    def test_two_part_numeric_version(self):
        self.assertEqual(parse_version('1.5'), (1, 5))

    def test_non_numeric_returns_empty(self):
        self.assertEqual(parse_version('dev'), ())

    def test_mixed_returns_empty(self):
        self.assertEqual(parse_version('0.20.0-rc1'), ())

    def test_empty_string_returns_empty(self):
        self.assertEqual(parse_version(''), ())

    def test_none_returns_empty(self):
        self.assertEqual(parse_version(None), ())

    def test_ordering_lower_lt_higher(self):
        self.assertLess(parse_version('0.19.0'), parse_version('0.20.0'))
        self.assertLess(parse_version('0.20.0'), parse_version('0.20.1'))


def _stub_changelog_entries(test):
    """Patch get_changelog_entries at its definition site for the test lifetime."""
    entries = [
        {'version': '0.20.0', 'title': 'Latest', 'html': '<p>new</p>'},
        {'version': '0.19.0', 'title': 'Older', 'html': '<p>old</p>'},
        {'version': '0.18.0', 'title': 'Oldest', 'html': '<p>oldest</p>'},
    ]
    patcher = patch.object(
        changelog_module, 'get_changelog_entries', return_value=entries,
    )
    patcher.start()
    test.addCleanup(patcher.stop)


class ChangelogPartialViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')
        self.client = Client()
        self.client.force_login(self.user)
        _stub_changelog_entries(self)

    def tearDown(self):
        cache.clear()

    def test_requires_auth(self):
        client = Client()
        resp = client.get('/changelog')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_first_visit_marks_latest_changelog_version_as_seen(self):
        self.assertIsNone(get_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY,
        ))
        resp = self.client.get('/changelog')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            get_setting(self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY),
            '0.20.0',
        )

    def test_first_visit_marks_all_entries_unread(self):
        resp = self.client.get('/changelog')
        entries = resp.context['entries']
        self.assertEqual([e['read'] for e in entries], [False, False, False])

    def test_returning_user_with_older_seen_version_marks_older_as_read(self):
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.19.0',
        )
        resp = self.client.get('/changelog')
        entries = resp.context['entries']
        read_by_version = {e['version']: e['read'] for e in entries}
        self.assertFalse(read_by_version['0.20.0'])  # new, just being seen
        self.assertTrue(read_by_version['0.19.0'])   # previously seen
        self.assertTrue(read_by_version['0.18.0'])   # older than previously seen

    def test_returning_user_at_current_version_marks_all_read(self):
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.20.0',
        )
        resp = self.client.get('/changelog')
        entries = resp.context['entries']
        self.assertEqual([e['read'] for e in entries], [True, True, True])

    def test_dev_last_seen_falls_back_to_equality(self):
        # If a user previously saw 'dev', only an entry literally tagged 'dev'
        # would count as read. Numeric entries stay unread until a real release.
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, 'dev',
        )
        resp = self.client.get('/changelog')
        entries = resp.context['entries']
        self.assertEqual([e['read'] for e in entries], [False, False, False])

    def test_empty_changelog_does_not_overwrite_existing_seen_value(self):
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.19.0',
        )
        with patch.object(
            changelog_module, 'get_changelog_entries', return_value=[],
        ):
            resp = self.client.get('/changelog')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            get_setting(self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY),
            '0.19.0',
        )


class ChangelogContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='bob', password='pass')
        # Mark onboarding completed so the changelog gating is exercised in
        # isolation; an onboarding-pending user always sees CHANGELOG_UNREAD
        # as False (the onboarding modal owns first-load).
        set_setting(self.user, 'core', 'onboarding_completed', True)
        _stub_changelog_entries(self)

    def tearDown(self):
        cache.clear()

    def _request(self, user):
        req = self.factory.get('/')
        req.user = user
        return req

    def test_anonymous_user_has_no_unread_flag(self):
        ctx = workspace_modules(self._request(AnonymousUser()))
        self.assertFalse(ctx['CHANGELOG_UNREAD'])

    def test_user_who_never_opened_modal_is_unread(self):
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx['CHANGELOG_UNREAD'])

    def test_user_who_saw_older_version_is_unread(self):
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.19.0',
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx['CHANGELOG_UNREAD'])

    def test_user_at_latest_changelog_version_is_not_unread(self):
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.20.0',
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx['CHANGELOG_UNREAD'])

    def test_empty_changelog_means_no_unread(self):
        with patch.object(
            changelog_module, 'get_changelog_entries', return_value=[],
        ):
            ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx['CHANGELOG_UNREAD'])
