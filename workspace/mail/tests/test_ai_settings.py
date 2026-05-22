from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.services.ai_settings import (
    MAIL_AI_FEATURES,
    is_mail_ai_feature_enabled,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


class IsMailAIFeatureEnabledTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')

    def tearDown(self):
        cache.clear()

    def test_defaults_to_true_when_no_setting(self):
        for feature in MAIL_AI_FEATURES:
            self.assertTrue(is_mail_ai_feature_enabled(self.user, feature))

    def test_explicit_false_disables_feature(self):
        set_setting(self.user, 'mail', 'ai_classify', False)
        self.assertFalse(is_mail_ai_feature_enabled(self.user, 'classify'))
        # The other features are untouched.
        self.assertTrue(is_mail_ai_feature_enabled(self.user, 'extract'))
        self.assertTrue(is_mail_ai_feature_enabled(self.user, 'manual'))

    def test_legacy_ai_enabled_false_is_inherited_as_default(self):
        # User turned the single legacy toggle off before the per-feature split.
        set_setting(self.user, 'mail', 'ai_enabled', False)
        for feature in MAIL_AI_FEATURES:
            self.assertFalse(is_mail_ai_feature_enabled(self.user, feature))

    def test_explicit_feature_setting_overrides_legacy_default(self):
        set_setting(self.user, 'mail', 'ai_enabled', False)
        set_setting(self.user, 'mail', 'ai_manual', True)
        # The opted-in feature is enabled, others stay off via the legacy fallback.
        self.assertTrue(is_mail_ai_feature_enabled(self.user, 'manual'))
        self.assertFalse(is_mail_ai_feature_enabled(self.user, 'classify'))
        self.assertFalse(is_mail_ai_feature_enabled(self.user, 'extract'))

    def test_unknown_feature_raises(self):
        with self.assertRaises(ValueError):
            is_mail_ai_feature_enabled(self.user, 'summarize')
