from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.core import changelog as changelog_module
from workspace.core.context_processors import (
    ONBOARDING_SETTING_KEY,
    ONBOARDING_SETTING_MODULE,
    workspace_modules,
)
from workspace.core.views_changelog import (
    CHANGELOG_SETTING_KEY,
    CHANGELOG_SETTING_MODULE,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


def _stub_changelog_with_latest(test, latest='0.20.0'):
    """Patch get_changelog_entries with a single fake entry so CHANGELOG_UNREAD can be exercised."""
    entries = [{'version': latest, 'title': 'Stub', 'html': '<p>x</p>'}]
    patcher = patch.object(
        changelog_module, 'get_changelog_entries', return_value=entries,
    )
    patcher.start()
    test.addCleanup(patcher.stop)


class OnboardingContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='alice', password='pass')
        _stub_changelog_with_latest(self)

    def tearDown(self):
        cache.clear()

    def _request(self, user):
        req = self.factory.get('/')
        req.user = user
        return req

    def test_anonymous_user_has_no_onboarding_flag(self):
        ctx = workspace_modules(self._request(AnonymousUser()))
        self.assertFalse(ctx['ONBOARDING_PENDING'])
        self.assertFalse(ctx['CHANGELOG_UNREAD'])

    def test_user_without_setting_is_pending(self):
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx['ONBOARDING_PENDING'])

    def test_user_with_setting_false_is_pending(self):
        set_setting(
            self.user, ONBOARDING_SETTING_MODULE, ONBOARDING_SETTING_KEY, False,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx['ONBOARDING_PENDING'])

    def test_user_with_setting_true_is_not_pending(self):
        set_setting(
            self.user, ONBOARDING_SETTING_MODULE, ONBOARDING_SETTING_KEY, True,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx['ONBOARDING_PENDING'])

    def test_pending_onboarding_suppresses_changelog_unread(self):
        # Brand-new user has unread changelog AND pending onboarding; the
        # onboarding modal owns the first-load slot, so the changelog
        # should NOT auto-open over it.
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx['ONBOARDING_PENDING'])
        self.assertFalse(ctx['CHANGELOG_UNREAD'])

    def test_completed_onboarding_lets_changelog_unread_through(self):
        set_setting(
            self.user, ONBOARDING_SETTING_MODULE, ONBOARDING_SETTING_KEY, True,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx['ONBOARDING_PENDING'])
        self.assertTrue(ctx['CHANGELOG_UNREAD'])

    def test_completed_onboarding_and_seen_changelog_is_quiet(self):
        set_setting(
            self.user, ONBOARDING_SETTING_MODULE, ONBOARDING_SETTING_KEY, True,
        )
        set_setting(
            self.user, CHANGELOG_SETTING_MODULE, CHANGELOG_SETTING_KEY, '0.20.0',
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx['ONBOARDING_PENDING'])
        self.assertFalse(ctx['CHANGELOG_UNREAD'])


