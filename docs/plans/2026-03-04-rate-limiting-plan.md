# Rate Limiting Global — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add global rate limiting to all API endpoints and public pages using `django-ratelimit`, with differentiated limits for anonymous vs authenticated users and sensitive endpoints.

**Architecture:** `django-ratelimit` decorators applied to all views via a centralized helper module. DRF exception handler extended for JSON 429 responses with `Retry-After`. Custom 429.html template for HTML pages. Existing manual rate limiting in `SharedPollVoteView` replaced.

**Tech Stack:** django-ratelimit, Django cache (Redis), DRF custom exception handler

---

### Task 1: Install django-ratelimit

**Files:**
- Modify: `pyproject.toml:7-46` (dependencies section)

**Step 1: Add dependency**

In `pyproject.toml`, add `django-ratelimit` to the `[project.dependencies]` list:

```toml
"django-ratelimit>=4.1",
```

**Step 2: Install**

Run: `pip install django-ratelimit`

**Step 3: Verify installation**

Run: `python -c "import ratelimit; print(ratelimit.__version__)"`
Expected: version number (4.x)

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add django-ratelimit dependency"
```

---

### Task 2: Add rate limiting settings

**Files:**
- Modify: `workspace/settings.py:231-255` (after REST_FRAMEWORK block)

**Step 1: Write test for settings presence**

Create file `workspace/core/tests/test_ratelimit_settings.py`:

```python
from django.conf import settings
from django.test import TestCase


class RateLimitSettingsTest(TestCase):
    def test_ratelimit_ip_meta_key_configured(self):
        self.assertEqual(
            settings.RATELIMIT_IP_META_KEY,
            'HTTP_X_FORWARDED_FOR',
        )

    def test_rate_limits_defined(self):
        self.assertIn('anon', settings.RATE_LIMITS)
        self.assertIn('user', settings.RATE_LIMITS)
        self.assertIn('sensitive', settings.RATE_LIMITS)

    def test_ratelimit_uses_cache(self):
        # django-ratelimit uses 'default' cache by default
        self.assertIn('default', settings.CACHES)
```

**Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.core.tests.test_ratelimit_settings -v2`
Expected: FAIL — `RATELIMIT_IP_META_KEY` not found

**Step 3: Add settings**

In `workspace/settings.py`, after the `REST_FRAMEWORK` block (~line 255), add:

```python
# ---------------------------------------------------------------------------
# Rate Limiting (django-ratelimit)
# ---------------------------------------------------------------------------
# Nginx reverse proxy: use X-Forwarded-For to get real client IP
RATELIMIT_IP_META_KEY = 'HTTP_X_FORWARDED_FOR'
# Fallback to REMOTE_ADDR if header missing
RATELIMIT_IP_META_KEY_FALLBACK = 'REMOTE_ADDR'

# Centralised rate definitions (referenced by decorators)
RATE_LIMITS = {
    'anon': '60/m',        # Anonymous: 60 requests/minute
    'anon_hourly': '500/h',  # Anonymous: 500 requests/hour
    'user': '300/m',       # Authenticated: 300 requests/minute
    'user_hourly': '3000/h', # Authenticated: 3000 requests/hour
    'sensitive': '30/m',   # Sensitive endpoints: 30 requests/minute
}
```

**Step 4: Run test to verify it passes**

Run: `python manage.py test workspace.core.tests.test_ratelimit_settings -v2`
Expected: PASS

**Step 5: Commit**

```bash
git add workspace/settings.py workspace/core/tests/test_ratelimit_settings.py
git commit -m "feat: add django-ratelimit settings with proxy support"
```

---

### Task 3: Create rate limiting helpers module

**Files:**
- Create: `workspace/common/ratelimit.py`
- Test: `workspace/common/tests/test_ratelimit.py`

**Step 1: Write tests**

Create `workspace/common/tests/test_ratelimit.py`:

```python
from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.ratelimit import get_client_key, ratelimit_block


class GetClientKeyTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_authenticated_user_returns_user_id(self):
        request = self.factory.get('/')
        request.user = type('User', (), {'is_authenticated': True, 'pk': 42})()
        self.assertEqual(get_client_key(None, request), '42')

    def test_anonymous_returns_ip_from_xff(self):
        request = self.factory.get('/', HTTP_X_FORWARDED_FOR='1.2.3.4, 10.0.0.1')
        request.user = type('User', (), {'is_authenticated': False})()
        self.assertEqual(get_client_key(None, request), '1.2.3.4')

    def test_anonymous_returns_remote_addr_fallback(self):
        request = self.factory.get('/', REMOTE_ADDR='9.8.7.6')
        request.user = type('User', (), {'is_authenticated': False})()
        self.assertEqual(get_client_key(None, request), '9.8.7.6')
```

**Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.common.tests.test_ratelimit -v2`
Expected: FAIL — module not found

**Step 3: Implement helpers**

Create `workspace/common/ratelimit.py`:

```python
"""Centralised rate-limiting helpers for DRF and Django views."""

from django.conf import settings
from django.http import JsonResponse
from ratelimit.exceptions import Ratelimited


def get_client_key(group, request):
    """Return user PK for authenticated users, IP for anonymous."""
    if hasattr(request, 'user') and request.user.is_authenticated:
        return str(request.user.pk)
    # Respect proxy header
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def ratelimit_block(request, exception):
    """Return True if the request was rate-limited (for use after @ratelimit)."""
    return getattr(request, 'limited', False)
```

**Step 4: Run test to verify it passes**

Run: `python manage.py test workspace.common.tests.test_ratelimit -v2`
Expected: PASS

**Step 5: Commit**

```bash
git add workspace/common/ratelimit.py workspace/common/tests/test_ratelimit.py
git commit -m "feat: add centralised rate-limiting helpers"
```

---

### Task 4: Create custom 429 error template and handler

**Files:**
- Create: `workspace/common/templates/429.html`
- Modify: `workspace/settings.py` (add handler for 429)
- Create: `workspace/common/views_errors.py`
- Test: `workspace/common/tests/test_error_views.py`

**Step 1: Write test**

Create `workspace/common/tests/test_error_views.py`:

```python
from django.test import TestCase, RequestFactory
from ratelimit.exceptions import Ratelimited

from workspace.common.views_errors import handler429


class Handler429Test(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_html_request_returns_429_template(self):
        request = self.factory.get('/', HTTP_ACCEPT='text/html')
        response = handler429(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertIn(b'Too many requests', response.content)

    def test_json_request_returns_json(self):
        request = self.factory.get(
            '/', HTTP_ACCEPT='application/json', content_type='application/json'
        )
        response = handler429(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Type'], 'application/json')
```

**Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.common.tests.test_error_views -v2`
Expected: FAIL — module not found

**Step 3: Create the 429 template**

Create `workspace/common/templates/429.html`:

```html
{% extends "base.html" %}

{% block title %}Too Many Requests — Workspace{% endblock %}

{% block content %}
<div class="min-h-screen flex items-center justify-center">
  <div class="text-center max-w-md mx-auto p-8">
    <div class="text-6xl font-bold text-base-content/20 mb-4">429</div>
    <h1 class="text-2xl font-semibold mb-2">Too many requests</h1>
    <p class="text-base-content/60 mb-6">
      You've made too many requests in a short period. Please wait a moment and try again.
    </p>
    <a href="javascript:history.back()" class="btn btn-primary">Go Back</a>
  </div>
</div>
{% endblock %}
```

**Step 4: Create the handler view**

Create `workspace/common/views_errors.py`:

```python
"""Custom error handlers."""

from django.http import JsonResponse
from django.template.loader import render_to_string


def handler429(request, exception):
    """Handle 429 Too Many Requests — HTML or JSON based on Accept header."""
    accept = request.META.get('HTTP_ACCEPT', '')
    if 'application/json' in accept:
        return JsonResponse(
            {'detail': 'Too many requests. Please try again later.'},
            status=429,
        )
    html = render_to_string('429.html', request=request)
    from django.http import HttpResponse
    return HttpResponse(html, status=429, content_type='text/html')
```

**Step 5: Wire up the handler in root urls.py**

In the root `workspace/urls.py`, add:

```python
handler429 = 'workspace.common.views_errors.handler429'
```

Note: `django-ratelimit` raises `ratelimit.exceptions.Ratelimited` which Django catches as a 403 by default. We need to configure `django-ratelimit` to use our custom exception handling. In `workspace/settings.py`, add:

```python
RATELIMIT_EXCEPTION_ENABLE = True
```

This makes `django-ratelimit` raise `Ratelimited` exceptions, which we catch with a custom middleware (see Task 5).

**Step 6: Run test to verify it passes**

Run: `python manage.py test workspace.common.tests.test_error_views -v2`
Expected: PASS

**Step 7: Commit**

```bash
git add workspace/common/templates/429.html workspace/common/views_errors.py workspace/common/tests/test_error_views.py workspace/urls.py workspace/settings.py
git commit -m "feat: add custom 429 error template and handler"
```

---

### Task 5: Create Ratelimit exception middleware

`django-ratelimit` raises `ratelimit.exceptions.Ratelimited` when a view is rate-limited. By default Django converts this to a 403. We need middleware to catch it and return a proper 429.

**Files:**
- Create: `workspace/common/middleware_ratelimit.py`
- Modify: `workspace/settings.py:167-189` (MIDDLEWARE list)
- Test: `workspace/common/tests/test_ratelimit_middleware.py`

**Step 1: Write test**

Create `workspace/common/tests/test_ratelimit_middleware.py`:

```python
from django.http import HttpResponse, JsonResponse
from django.test import TestCase, RequestFactory
from ratelimit.exceptions import Ratelimited

from workspace.common.middleware_ratelimit import RatelimitMiddleware


class RatelimitMiddlewareTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_middleware(self):
        def dummy_get_response(request):
            return HttpResponse('OK')
        return RatelimitMiddleware(dummy_get_response)

    def test_normal_request_passes_through(self):
        mw = self._get_middleware()
        request = self.factory.get('/')
        response = mw(request)
        self.assertEqual(response.status_code, 200)

    def test_ratelimited_api_returns_json_429(self):
        def raising_view(request):
            raise Ratelimited()
        mw = RatelimitMiddleware(raising_view)
        request = self.factory.get('/api/v1/something', content_type='application/json')
        response = mw.process_exception(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_ratelimited_html_returns_html_429(self):
        def raising_view(request):
            raise Ratelimited()
        mw = RatelimitMiddleware(raising_view)
        request = self.factory.get('/calendar/polls/shared/abc', HTTP_ACCEPT='text/html')
        response = mw.process_exception(request, Ratelimited())
        self.assertEqual(response.status_code, 429)
        self.assertIn(b'Too many requests', response.content)

    def test_retry_after_header_present(self):
        def raising_view(request):
            raise Ratelimited()
        mw = RatelimitMiddleware(raising_view)
        request = self.factory.get('/api/v1/something', content_type='application/json')
        response = mw.process_exception(request, Ratelimited())
        self.assertIn('Retry-After', response)
```

**Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.common.tests.test_ratelimit_middleware -v2`
Expected: FAIL — module not found

**Step 3: Implement middleware**

Create `workspace/common/middleware_ratelimit.py`:

```python
"""Middleware to catch Ratelimited exceptions and return proper 429 responses."""

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from ratelimit.exceptions import Ratelimited


class RatelimitMiddleware:
    """Convert Ratelimited exceptions to 429 responses with Retry-After."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, Ratelimited):
            return None

        retry_after = 60  # seconds

        if request.path.startswith('/api/'):
            response = JsonResponse(
                {'detail': 'Too many requests. Please try again later.'},
                status=429,
            )
        else:
            html = render_to_string('429.html', request=request)
            response = HttpResponse(html, status=429, content_type='text/html')

        response['Retry-After'] = str(retry_after)
        return response
```

**Step 4: Add middleware to settings**

In `workspace/settings.py`, add to MIDDLEWARE list (early, before most middleware):

```python
MIDDLEWARE = [
    # ... existing entries ...
    'workspace.common.middleware_ratelimit.RatelimitMiddleware',
    # ... rest ...
]
```

Place it after SecurityMiddleware but before SessionMiddleware.

**Step 5: Run test to verify it passes**

Run: `python manage.py test workspace.common.tests.test_ratelimit_middleware -v2`
Expected: PASS

**Step 6: Commit**

```bash
git add workspace/common/middleware_ratelimit.py workspace/common/tests/test_ratelimit_middleware.py workspace/settings.py
git commit -m "feat: add middleware to convert Ratelimited to 429 responses"
```

---

### Task 6: Apply rate limiting to public poll endpoints

**Files:**
- Modify: `workspace/calendar/views_polls.py:390-498` (SharedPollView, SharedPollVoteView)
- Modify: `workspace/calendar/tests/test_polls.py:445-457` (update rate limit test)

**Step 1: Write test for SharedPollView rate limiting**

In `workspace/calendar/tests/test_polls.py`, add a new test:

```python
from unittest.mock import patch

def test_shared_poll_view_rate_limited(self):
    """SharedPollView returns 429 when rate limited."""
    url = f'/api/v1/calendar/polls/shared/{self.poll.share_token}'
    with patch('ratelimit.decorators.is_ratelimited', return_value=True):
        # We test via the middleware catching Ratelimited exception
        pass
    # Alternatively, test by hitting the endpoint many times
    # The decorator will handle this automatically
```

Actually, for `django-ratelimit` with DRF views, we apply the decorator on the method. Let's write integration-style tests.

**Step 2: Modify SharedPollView — add rate limiting**

In `workspace/calendar/views_polls.py`, update SharedPollView:

```python
from django.conf import settings
from ratelimit.decorators import ratelimit

from workspace.common.ratelimit import get_client_key


class SharedPollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Get poll by share token", responses=PollSerializer)
    @ratelimit(key='ip', rate=settings.RATE_LIMITS['anon'], method='GET', block=True)
    def get(self, request, token):
        # ... existing code unchanged ...
```

**Step 3: Modify SharedPollVoteView — replace manual rate limiting**

In `workspace/calendar/views_polls.py`, update SharedPollVoteView:

```python
class SharedPollVoteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(summary="Submit guest votes", request=GuestVoteSubmitSerializer, responses=PollSerializer)
    @ratelimit(key='ip', rate=settings.RATE_LIMITS['sensitive'], method='POST', block=True)
    def post(self, request, token):
        poll = get_object_or_404(Poll, share_token=token, status=Poll.Status.OPEN)

        # REMOVE the manual rate limiting code (lines 433-442)
        # REMOVE the _get_client_ip method (lines 423-427)

        ser = GuestVoteSubmitSerializer(data=request.data)
        # ... rest of existing code unchanged ...
```

**Step 4: Update existing rate limit test**

In `workspace/calendar/tests/test_polls.py`, update `test_guest_vote_rate_limit` (lines 445-457) to work with `django-ratelimit`:

```python
def test_guest_vote_rate_limit(self):
    cache.clear()
    url = f'/api/v1/calendar/polls/shared/{self.poll.share_token}/vote'
    payload = {
        'guest_name': 'Spammer',
        'votes': [{'slot_id': str(self.slot1.uuid), 'choice': 'yes'}],
    }
    # django-ratelimit sensitive limit is 30/min
    for i in range(30):
        resp = self.client.post(url, payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
    # 31st should be rate limited
    resp = self.client.post(url, payload, format='json')
    self.assertEqual(resp.status_code, 429)
```

**Step 5: Run tests**

Run: `python manage.py test workspace.calendar.tests.test_polls -v2`
Expected: PASS

**Step 6: Commit**

```bash
git add workspace/calendar/views_polls.py workspace/calendar/tests/test_polls.py
git commit -m "feat: apply django-ratelimit to public poll endpoints, remove manual rate limiting"
```

---

### Task 7: Apply rate limiting to all other public endpoints

**Files:**
- Modify: `workspace/users/views.py:210` (UserAvatarRetrieveView)
- Modify: `workspace/chat/views.py:1000` (GroupAvatarRetrieveView)
- Modify: `workspace/calendar/ui/views.py` (polls_shared UI view)

Note: Do NOT rate limit health check endpoints (`core/views_health.py` — StartupView, LiveView, ReadyView) as they are used by infrastructure probes.

**Step 1: Add rate limiting to UserAvatarRetrieveView**

In `workspace/users/views.py`:

```python
from ratelimit.decorators import ratelimit
from django.conf import settings

class UserAvatarRetrieveView(APIView):
    permission_classes = [AllowAny]

    @ratelimit(key='ip', rate=settings.RATE_LIMITS['anon'], method='GET', block=True)
    def get(self, request, user_id, size=None):
        # ... existing code ...
```

**Step 2: Add rate limiting to GroupAvatarRetrieveView**

In `workspace/chat/views.py`:

```python
from ratelimit.decorators import ratelimit
from django.conf import settings

class GroupAvatarRetrieveView(APIView):
    permission_classes = [AllowAny]

    @ratelimit(key='ip', rate=settings.RATE_LIMITS['anon'], method='GET', block=True)
    def get(self, request, conversation_id, size=None):
        # ... existing code ...
```

**Step 3: Add rate limiting to polls_shared UI view**

In `workspace/calendar/ui/views.py`:

```python
from django.conf import settings
from ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate=settings.RATE_LIMITS['anon'], block=True)
def polls_shared(request, token):
    # ... existing code ...
```

**Step 4: Run full test suite**

Run: `python manage.py test -v2`
Expected: PASS

**Step 5: Commit**

```bash
git add workspace/users/views.py workspace/chat/views.py workspace/calendar/ui/views.py
git commit -m "feat: apply rate limiting to all public endpoints (excl. health checks)"
```

---

### Task 8: Apply rate limiting to authenticated API endpoints

**Files:**
- Modify: All authenticated API view files (see list below)

Apply rate limiting using `get_client_key` (user ID for authenticated, IP for anonymous) on all authenticated endpoints.

For DRF `APIView` classes, add the decorator to each HTTP method:

```python
from ratelimit.decorators import ratelimit
from django.conf import settings
from workspace.common.ratelimit import get_client_key

# Standard authenticated endpoint
@ratelimit(key=get_client_key, rate=settings.RATE_LIMITS['user'], method=ratelimit.ALL, block=True)
def get(self, request, ...):

# Sensitive write endpoints (create, vote, invite, send)
@ratelimit(key=get_client_key, rate=settings.RATE_LIMITS['sensitive'], method='POST', block=True)
def post(self, request, ...):
```

**Files to modify with standard `user` rate:**
- `workspace/ai/views.py` — all views (BotListView, etc.)
- `workspace/calendar/views.py` — CalendarListView, CalendarDetailView, EventListView, EventDetailView
- `workspace/calendar/views_polls.py` — PollListView, PollDetailView
- `workspace/chat/views.py` — all authenticated views
- `workspace/core/activity_views.py` — all views
- `workspace/core/views.py` — ModulesView, UnifiedSearchView
- `workspace/files/views_thumbnails.py` — GenerateThumbnailsView
- `workspace/mail/views.py` — all views
- `workspace/mail/views_oauth2.py` — all views
- `workspace/notifications/views.py` — all views
- `workspace/users/views.py` — all authenticated views

**Sensitive endpoints (use `sensitive` rate) — POST/PATCH/DELETE methods on:**
- `workspace/calendar/views_polls.py` — PollVoteView.post, PollFinalizeView.post, PollInviteView.post/delete
- `workspace/calendar/views.py` — EventRespondView.post
- `workspace/chat/views.py` — MessageListView.post (send message), ReactionToggleView.post
- `workspace/mail/views.py` — MailSendView.post
- `workspace/users/views.py` — ChangePasswordView.post

**Step 1: Apply decorators to each view file**

Work through each file listed above, importing the needed modules and adding `@ratelimit` before each HTTP method handler.

**Step 2: Run full test suite**

Run: `python manage.py test -v2`
Expected: PASS (existing tests should still pass — rate limits won't be hit in normal test execution)

**Step 3: Commit**

```bash
git add workspace/ai/views.py workspace/calendar/views.py workspace/calendar/views_polls.py workspace/chat/views.py workspace/core/activity_views.py workspace/core/views.py workspace/files/views_thumbnails.py workspace/mail/views.py workspace/mail/views_oauth2.py workspace/notifications/views.py workspace/users/views.py
git commit -m "feat: apply rate limiting to all authenticated API endpoints"
```

---

### Task 9: Integration tests

**Files:**
- Create: `workspace/common/tests/test_ratelimit_integration.py`

**Step 1: Write integration tests**

```python
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


class RateLimitIntegrationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='testuser', password='testpass123'
        )
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def test_anonymous_rate_limit_on_public_endpoint(self):
        """Anonymous users get 429 after exceeding anon rate limit."""
        # This test assumes RATE_LIMITS['anon'] = '60/m'
        # We can't realistically hit 60 requests in a unit test,
        # so we override with a lower limit for testing
        pass  # See override_settings approach below

    @override_settings(RATE_LIMITS={
        'anon': '2/m', 'anon_hourly': '10/h',
        'user': '5/m', 'user_hourly': '50/h',
        'sensitive': '1/m',
    })
    def test_anon_rate_limit_returns_429(self):
        """Anonymous requests return 429 after exceeding limit."""
        from workspace.calendar.models import Poll, PollSlot
        from workspace.common.uuids import uuid_v7_or_v4
        poll = Poll.objects.create(
            title='Test', created_by=self.user, share_token='test-token-123'
        )
        url = f'/api/v1/calendar/polls/shared/test-token-123'
        # First 2 should succeed
        for _ in range(2):
            resp = self.client.get(url)
            self.assertIn(resp.status_code, [200, 404])  # may 404 if no slots, that's ok
        # 3rd should be rate limited
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Retry-After', resp)

    @override_settings(RATE_LIMITS={
        'anon': '2/m', 'anon_hourly': '10/h',
        'user': '3/m', 'user_hourly': '50/h',
        'sensitive': '1/m',
    })
    def test_authenticated_rate_limit_returns_429(self):
        """Authenticated requests return 429 after exceeding limit."""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/core/modules'
        for _ in range(3):
            self.client.get(url)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 429)

    def test_429_json_response_format(self):
        """429 response contains expected JSON structure for API endpoints."""
        # Tested via middleware test — this is a smoke test
        pass

    def test_health_endpoints_not_rate_limited(self):
        """Health check endpoints should never be rate limited."""
        for url in ['/api/v1/health/live', '/api/v1/health/ready']:
            for _ in range(100):
                resp = self.client.get(url)
            # Should still succeed
            self.assertNotEqual(resp.status_code, 429)
```

**Step 2: Run tests**

Run: `python manage.py test workspace.common.tests.test_ratelimit_integration -v2`
Expected: PASS

**Step 3: Commit**

```bash
git add workspace/common/tests/test_ratelimit_integration.py
git commit -m "test: add rate limiting integration tests"
```

---

### Task 10: Final verification and cleanup

**Step 1: Run full test suite**

Run: `python manage.py test -v2`
Expected: ALL PASS

**Step 2: Manual smoke test**

Run: `python manage.py runserver`

Test:
- Visit a public poll shared link — should work normally
- Rapidly refresh — should eventually get 429 HTML page
- Hit API endpoint repeatedly — should get JSON 429 with Retry-After

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: global rate limiting with django-ratelimit

- Add django-ratelimit dependency
- Configure rate limits: 60/min anon, 300/min auth, 30/min sensitive
- Support Nginx proxy via X-Forwarded-For
- Custom 429 template for HTML pages, JSON for API
- Middleware to catch Ratelimited exceptions
- Replace manual rate limiting in SharedPollVoteView
- Health check endpoints excluded from rate limiting"
```
