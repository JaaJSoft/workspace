from django.conf import settings
from django.test import TestCase


class RateLimitSettingsTest(TestCase):
    def test_ratelimit_ip_meta_key_configured(self):
        self.assertEqual(
            settings.RATELIMIT_IP_META_KEY,
            'workspace.common.ratelimit.get_client_ip',
        )

    def test_rate_limits_defined(self):
        self.assertIn('anon', settings.RATE_LIMITS)
        self.assertIn('user', settings.RATE_LIMITS)
        self.assertIn('sensitive', settings.RATE_LIMITS)

    def test_ratelimit_uses_cache(self):
        self.assertIn('default', settings.CACHES)
