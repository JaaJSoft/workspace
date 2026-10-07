"""Redis cache settings, as they are evaluated when a Redis URL is configured."""

import importlib
import os
from unittest.mock import patch

import redis
from django.test import SimpleTestCase
from django_redis.pool import ConnectionFactory

from workspace.settings import cache as cache_settings


class RedisClientPoolTests(SimpleTestCase):
    def setUp(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}):
            self.settings = importlib.reload(cache_settings)
        self.addCleanup(importlib.reload, cache_settings)

    def test_a_full_pool_makes_a_request_wait_instead_of_failing_it(self):
        """Under gevent every open SSE stream of a worker may poll Redis at the
        same moment. Redis' default pool raises MaxConnectionsError past its
        size, failing whatever request asked for a connection then."""
        for alias in ("default", "sessions"):
            with self.subTest(alias=alias):
                cache = self.settings.CACHES[alias]
                factory = ConnectionFactory(cache["OPTIONS"])
                pool = factory.get_connection_pool(
                    factory.make_connection_params(cache["LOCATION"])
                )

                self.assertIsInstance(pool, redis.BlockingConnectionPool)
                self.assertGreater(pool.timeout, 0)
