"""increment_counter, especially the ValueError fallback.

cache.add followed by cache.incr is two round trips: if the key is gone by
the second one (TTL boundary, or evicted under memory pressure), both
django-redis and Django's LocMemCache raise ValueError on an incr of a
missing key. Every caller of this helper sits on an unauthenticated,
anonymous-reachable endpoint, so that ValueError must never reach the view
as an uncaught 500.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from workspace.common.rate_limit import increment_counter


class IncrementCounterTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_first_call_starts_at_one(self):
        self.assertEqual(increment_counter("rl:a", 60), 1)

    def test_repeated_calls_count_up(self):
        increment_counter("rl:b", 60)
        increment_counter("rl:b", 60)
        self.assertEqual(increment_counter("rl:b", 60), 3)

    def test_distinct_keys_do_not_share_a_counter(self):
        increment_counter("rl:c", 60)
        increment_counter("rl:c", 60)
        self.assertEqual(increment_counter("rl:d", 60), 1)

    def test_key_evicted_between_add_and_incr_restarts_at_one_without_raising(self):
        # Reproduces the race the docstring describes: cache.add sees no key
        # (or a genuinely absent one) and cache.incr, reached a moment
        # later, finds it gone.
        with patch.object(cache, "incr", side_effect=ValueError("key not found")):
            result = increment_counter("rl:e", 60)
        self.assertEqual(result, 1)

    def test_after_the_evicted_incr_the_counter_keeps_working(self):
        with patch.object(cache, "incr", side_effect=ValueError("key not found")):
            increment_counter("rl:f", 60)
        # The fallback's own cache.set(key, 1, ...) must have actually
        # landed - a real cache.incr on the next call proves it, since incr
        # on a still-missing key would raise for real this time.
        self.assertEqual(increment_counter("rl:f", 60), 2)
