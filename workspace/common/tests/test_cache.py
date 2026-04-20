from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from workspace.common.cache import (
    _build_key,
    cached,
    cached_response,
    invalidate,
    invalidate_tags,
)

User = get_user_model()
factory = APIRequestFactory()


# ── Test views ────────────────────────────────────────────────────────

class PerUserView(APIView):
    call_count = 0

    @cached_response(300)
    def get(self, request):
        PerUserView.call_count += 1
        return Response({'value': PerUserView.call_count})


class GlobalView(APIView):
    call_count = 0

    @cached_response(300, per_user=False)
    def get(self, request):
        GlobalView.call_count += 1
        return Response({'value': GlobalView.call_count})


class ErrorView(APIView):
    @cached_response(300)
    def get(self, request):
        return Response({'error': 'bad'}, status=400)

    @cached_response(300)
    def post(self, request):
        return Response({'created': True}, status=201)


# ── _build_key tests ─────────────────────────────────────────────────

class BuildKeyTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='keyuser', password='pass')

    def test_global_key(self):
        request = factory.get('/')
        request.user = self.user
        key = _build_key('MyView', request, per_user=False)
        self.assertEqual(key, 'view:MyView')

    def test_per_user_key(self):
        request = factory.get('/')
        request.user = self.user
        key = _build_key('MyView', request, per_user=True)
        self.assertEqual(key, f'view:MyView:u:{self.user.pk}')

    def test_query_params_included(self):
        request = factory.get('/', {'q': 'hello', 'limit': '10'})
        request.user = self.user
        key = _build_key('MyView', request, per_user=True)
        self.assertIn('q:', key)
        # Same params in different order → same key
        request2 = factory.get('/', {'limit': '10', 'q': 'hello'})
        request2.user = self.user
        key2 = _build_key('MyView', request2, per_user=True)
        self.assertEqual(key, key2)

    def test_different_params_different_key(self):
        request1 = factory.get('/', {'q': 'hello'})
        request1.user = self.user
        request2 = factory.get('/', {'q': 'world'})
        request2.user = self.user
        self.assertNotEqual(
            _build_key('MyView', request1, per_user=True),
            _build_key('MyView', request2, per_user=True),
        )

    def test_anonymous_user_no_user_segment(self):
        from django.contrib.auth.models import AnonymousUser
        request = factory.get('/')
        request.user = AnonymousUser()
        key = _build_key('MyView', request, per_user=True)
        self.assertEqual(key, 'view:MyView')


# ── cached_response decorator tests ──────────────────────────────────

class CacheResponsePerUserTests(TestCase):

    def setUp(self):
        cache.clear()
        PerUserView.call_count = 0
        self.user = User.objects.create_user(username='cacheuser', password='pass')
        self.view = PerUserView.as_view()

    def test_first_call_executes_view(self):
        request = factory.get('/')
        request.user = self.user
        response = self.view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['value'], 1)

    def test_second_call_returns_cached(self):
        request = factory.get('/')
        request.user = self.user
        self.view(request)
        response = self.view(request)
        self.assertEqual(response.data['value'], 1)  # still 1, not 2
        self.assertEqual(PerUserView.call_count, 1)

    def test_different_users_get_separate_cache(self):
        user2 = User.objects.create_user(username='other', password='pass')
        req1 = factory.get('/')
        req1.user = self.user
        req2 = factory.get('/')
        req2.user = user2

        self.view(req1)
        self.view(req2)

        self.assertEqual(PerUserView.call_count, 2)

    def test_different_query_params_separate_cache(self):
        req1 = factory.get('/', {'page': '1'})
        req1.user = self.user
        req2 = factory.get('/', {'page': '2'})
        req2.user = self.user

        self.view(req1)
        self.view(req2)

        self.assertEqual(PerUserView.call_count, 2)


class CacheResponseGlobalTests(TestCase):

    def setUp(self):
        cache.clear()
        GlobalView.call_count = 0
        self.user1 = User.objects.create_user(username='global1', password='pass')
        self.user2 = User.objects.create_user(username='global2', password='pass')
        self.view = GlobalView.as_view()

    def test_global_cache_shared_across_users(self):
        req1 = factory.get('/')
        req1.user = self.user1
        req2 = factory.get('/')
        req2.user = self.user2

        self.view(req1)
        self.view(req2)

        self.assertEqual(GlobalView.call_count, 1)  # shared cache


class CacheResponseEdgeCaseTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='edgeuser', password='pass')

    def test_error_responses_not_cached(self):
        view = ErrorView.as_view()
        request = factory.get('/')
        request.user = self.user

        view(request)
        # Should not be in cache
        key = _build_key('ErrorView', request, per_user=True)
        self.assertIsNone(cache.get(key))

    def test_post_bypasses_cache(self):
        view = ErrorView.as_view()
        request = factory.post('/')
        request.user = self.user

        response = view(request)
        self.assertEqual(response.status_code, 201)
        # POST should not read from cache
        key = _build_key('ErrorView', request, per_user=True)
        self.assertIsNone(cache.get(key))


# ── invalidate tests ─────────────────────────────────────────────────

class InvalidateTests(TestCase):

    def setUp(self):
        cache.clear()
        PerUserView.call_count = 0
        GlobalView.call_count = 0
        self.user = User.objects.create_user(username='invuser', password='pass')

    def test_invalidate_per_user(self):
        view = PerUserView.as_view()
        request = factory.get('/')
        request.user = self.user

        view(request)
        self.assertEqual(PerUserView.call_count, 1)

        invalidate('PerUserView', user=self.user)

        view(request)
        self.assertEqual(PerUserView.call_count, 2)  # re-executed after invalidation

    def test_invalidate_global(self):
        view = GlobalView.as_view()
        request = factory.get('/')
        request.user = self.user

        view(request)
        self.assertEqual(GlobalView.call_count, 1)

        invalidate('GlobalView')

        view(request)
        self.assertEqual(GlobalView.call_count, 2)

    def test_invalidate_user_id_int(self):
        """Can pass raw user ID instead of user object."""
        view = PerUserView.as_view()
        request = factory.get('/')
        request.user = self.user

        view(request)
        invalidate('PerUserView', user=self.user.pk)

        view(request)
        self.assertEqual(PerUserView.call_count, 2)

    def test_invalidate_one_user_keeps_other(self):
        """Invalidating one user's cache does not affect another's."""
        user2 = User.objects.create_user(username='other2', password='pass')
        view = PerUserView.as_view()

        req1 = factory.get('/')
        req1.user = self.user
        req2 = factory.get('/')
        req2.user = user2

        view(req1)
        view(req2)
        self.assertEqual(PerUserView.call_count, 2)

        invalidate('PerUserView', user=self.user)

        view(req1)  # re-executes → count 3
        view(req2)  # still cached → count stays 3
        self.assertEqual(PerUserView.call_count, 3)


# ── cached + invalidate_tags tests ────────────────────────────────

class CachedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.calls = []

    def tearDown(self):
        cache.clear()

    def test_memoizes_by_args(self):
        @cached(key=lambda x: f'k:{x}', ttl=60)
        def f(x):
            self.calls.append(x)
            return x * 2

        self.assertEqual(f(1), 2)
        self.assertEqual(f(1), 2)
        self.assertEqual(f(2), 4)
        self.assertEqual(self.calls, [1, 2])

    def test_static_key_and_no_tags(self):
        @cached(key='global:v', ttl=60)
        def f():
            self.calls.append(1)
            return 'value'

        self.assertEqual(f(), 'value')
        self.assertEqual(f(), 'value')
        self.assertEqual(self.calls, [1])

    def test_tag_invalidation_evicts_single_entry(self):
        @cached(
            key=lambda x: f'k:{x}', ttl=60,
            tags=lambda x: [f't:{x}'],
        )
        def f(x):
            self.calls.append(x)
            return x

        f(1); f(1)
        invalidate_tags('t:1')
        f(1)
        self.assertEqual(self.calls, [1, 1])

    def test_tag_invalidation_does_not_affect_other_tags(self):
        @cached(
            key=lambda x: f'k:{x}', ttl=60,
            tags=lambda x: [f't:{x}'],
        )
        def f(x):
            self.calls.append(x)
            return x

        f(1); f(2)
        invalidate_tags('t:1')
        f(1); f(2)
        self.assertEqual(self.calls, [1, 2, 1])

    def test_multi_tag_any_tag_evicts_entry(self):
        @cached(
            key=lambda a, b: f'k:{a}:{b}', ttl=60,
            tags=lambda a, b: [f'a:{a}', f'b:{b}'],
        )
        def f(a, b):
            self.calls.append((a, b))
            return (a, b)

        f(1, 2)
        invalidate_tags('a:1')
        f(1, 2)
        invalidate_tags('b:2')
        f(1, 2)
        self.assertEqual(self.calls, [(1, 2), (1, 2), (1, 2)])

    def test_caches_falsy_values(self):
        """None/0/'' must still be cached — using a sentinel to detect miss."""
        @cached(key='k', ttl=60)
        def f():
            self.calls.append(1)
            return None

        f(); f(); f()
        self.assertEqual(self.calls, [1])

    def test_invalidate_unknown_tag_is_safe(self):
        # No entries with this tag — should not error
        invalidate_tags('never-used')
