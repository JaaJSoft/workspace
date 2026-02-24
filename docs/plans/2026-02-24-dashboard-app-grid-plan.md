# Dashboard App Grid Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add badge provider infrastructure to the module registry and redesign the dashboard modules grid as a compact dock with per-module badge counts.

**Architecture:** Each module registers a `BadgeProviderInfo` callback in the module registry (same pattern as `SearchProviderInfo`). The dashboard view calls `registry.get_badge_counts(user)` to collect all counts, then passes them to the template. The modules grid template is redesigned as a compact horizontal dock with badge overlays.

**Tech Stack:** Django 6.0, DaisyUI/Tailwind CSS, Alpine.js, Lucide Icons

**Design doc:** `docs/plans/2026-02-24-dashboard-app-grid-design.md`

---

### Task 1: Add BadgeProviderInfo to module registry

**Files:**
- Modify: `workspace/core/module_registry.py:1-89`
- Test: `workspace/core/tests.py` (create)

**Step 1: Write the failing tests**

Create `workspace/core/tests.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.module_registry import BadgeProviderInfo, ModuleInfo, ModuleRegistry

User = get_user_model()


class BadgeProviderRegistryTests(TestCase):

    def setUp(self):
        self.registry = ModuleRegistry()
        self.registry.register(ModuleInfo(
            name='Chat', slug='chat', description='Chat module',
            icon='message-circle', color='info', url='/chat', order=10,
        ))
        self.registry.register(ModuleInfo(
            name='Calendar', slug='calendar', description='Calendar module',
            icon='calendar', color='accent', url='/calendar', order=20,
        ))
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )

    def test_register_badge_provider(self):
        provider = BadgeProviderInfo(module_slug='chat', badge_fn=lambda u: 5)
        self.registry.register_badge_provider(provider)
        counts = self.registry.get_badge_counts(self.user)
        self.assertEqual(counts, {'chat': 5})

    def test_register_badge_provider_unknown_module_raises(self):
        provider = BadgeProviderInfo(module_slug='unknown', badge_fn=lambda u: 0)
        with self.assertRaises(ValueError):
            self.registry.register_badge_provider(provider)

    def test_duplicate_badge_provider_raises(self):
        provider = BadgeProviderInfo(module_slug='chat', badge_fn=lambda u: 0)
        self.registry.register_badge_provider(provider)
        with self.assertRaises(ValueError):
            self.registry.register_badge_provider(provider)

    def test_get_badge_counts_multiple_providers(self):
        self.registry.register_badge_provider(
            BadgeProviderInfo(module_slug='chat', badge_fn=lambda u: 3),
        )
        self.registry.register_badge_provider(
            BadgeProviderInfo(module_slug='calendar', badge_fn=lambda u: 7),
        )
        counts = self.registry.get_badge_counts(self.user)
        self.assertEqual(counts, {'chat': 3, 'calendar': 7})

    def test_get_badge_counts_empty_when_no_providers(self):
        counts = self.registry.get_badge_counts(self.user)
        self.assertEqual(counts, {})

    def test_get_badge_counts_handles_provider_exception(self):
        def failing_fn(u):
            raise RuntimeError("oops")

        self.registry.register_badge_provider(
            BadgeProviderInfo(module_slug='chat', badge_fn=failing_fn),
        )
        # Should not raise; returns 0 for the failing provider
        counts = self.registry.get_badge_counts(self.user)
        self.assertEqual(counts, {'chat': 0})
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest workspace/core/tests.py -v`
Expected: FAIL — `ImportError: cannot import name 'BadgeProviderInfo'`

**Step 3: Implement BadgeProviderInfo and registry methods**

In `workspace/core/module_registry.py`, add after the `SearchProviderInfo` dataclass (line 38):

```python
@dataclass(frozen=True)
class BadgeProviderInfo:
    module_slug: str
    badge_fn: Callable  # signature: (user) -> int
```

In the `ModuleRegistry` class, add these methods:

```python
def register_badge_provider(self, provider: BadgeProviderInfo):
    with self._lock:
        if provider.module_slug not in self._modules:
            raise ValueError(
                f"Module '{provider.module_slug}' must be registered before its badge provider"
            )
        if provider.module_slug in self._badge_providers:
            raise ValueError(
                f"Badge provider for '{provider.module_slug}' is already registered"
            )
        self._badge_providers[provider.module_slug] = provider

def get_badge_counts(self, user) -> dict[str, int]:
    counts = {}
    for slug, provider in self._badge_providers.items():
        try:
            counts[slug] = provider.badge_fn(user)
        except Exception:
            logger.exception("Badge provider '%s' failed", slug)
            counts[slug] = 0
    return counts
```

Also add `self._badge_providers: dict[str, BadgeProviderInfo] = {}` in `__init__`.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest workspace/core/tests.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add workspace/core/module_registry.py workspace/core/tests.py
git commit -m "feat(core): add badge provider infrastructure to module registry"
```

---

### Task 2: Register badge provider for Chat module

**Files:**
- Modify: `workspace/chat/apps.py:1-33`
- Test: `workspace/chat/tests.py` (append)

**Step 1: Write the failing test**

Append to `workspace/chat/tests.py`:

```python
class ChatBadgeProviderTests(ChatTestMixin, TestCase):
    """Tests for the chat badge provider."""

    def test_badge_returns_unread_count(self):
        # Give member some unread messages
        ConversationMember.objects.filter(
            conversation=self.group, user=self.member,
        ).update(unread_count=3)
        ConversationMember.objects.filter(
            conversation=self.dm, user=self.member,
        ).update(unread_count=2)

        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.member)
        self.assertEqual(counts.get('chat'), 5)

    def test_badge_returns_zero_when_no_unread(self):
        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.creator)
        self.assertEqual(counts.get('chat'), 0)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest workspace/chat/tests.py::ChatBadgeProviderTests -v`
Expected: FAIL — `'chat'` not in counts (no badge provider registered yet)

**Step 3: Register the badge provider in chat/apps.py**

In `workspace/chat/apps.py`, inside the `ready()` method, add after the SSE registration:

```python
from workspace.core.module_registry import BadgeProviderInfo

def _chat_badge(user):
    from workspace.chat.services import get_unread_counts
    return get_unread_counts(user).get('total', 0)

registry.register_badge_provider(BadgeProviderInfo(
    module_slug='chat',
    badge_fn=_chat_badge,
))
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest workspace/chat/tests.py::ChatBadgeProviderTests -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add workspace/chat/apps.py workspace/chat/tests.py
git commit -m "feat(chat): register badge provider for unread message count"
```

---

### Task 3: Register badge provider for Calendar module

**Files:**
- Modify: `workspace/calendar/apps.py:1-35`
- Test: `workspace/calendar/tests/test_calendar.py` (append)

**Step 1: Write the failing test**

Append to `workspace/calendar/tests/test_calendar.py`:

```python
class CalendarBadgeProviderTests(CalendarTestMixin, TestCase):
    """Tests for the calendar badge provider."""

    def test_badge_returns_upcoming_event_count(self):
        # self.event is already 1 day in the future (within 7 days)
        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.owner)
        self.assertEqual(counts.get('calendar'), 1)

    def test_badge_includes_events_as_member(self):
        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.member)
        self.assertEqual(counts.get('calendar'), 1)

    def test_badge_excludes_past_events(self):
        self.event.start = timezone.now() - timedelta(days=1)
        self.event.end = timezone.now() - timedelta(hours=23)
        self.event.save()
        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.owner)
        self.assertEqual(counts.get('calendar'), 0)

    def test_badge_excludes_events_beyond_7_days(self):
        self.event.start = timezone.now() + timedelta(days=10)
        self.event.end = timezone.now() + timedelta(days=10, hours=1)
        self.event.save()
        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.owner)
        self.assertEqual(counts.get('calendar'), 0)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest workspace/calendar/tests/test_calendar.py::CalendarBadgeProviderTests -v`
Expected: FAIL — `'calendar'` not in counts

**Step 3: Register the badge provider in calendar/apps.py**

In `workspace/calendar/apps.py`, inside the `ready()` method, add after the last `register_search_provider` call:

```python
from workspace.core.module_registry import BadgeProviderInfo

def _calendar_badge(user):
    from datetime import timedelta
    from django.db.models import Q
    from django.utils import timezone
    from workspace.calendar.models import Event, EventMember
    now = timezone.now()
    return Event.objects.filter(
        Q(owner=user) | Q(members__user=user, members__status__in=[
            EventMember.Status.ACCEPTED, EventMember.Status.PENDING,
        ]),
        start__gte=now,
        start__lte=now + timedelta(days=7),
    ).distinct().count()

registry.register_badge_provider(BadgeProviderInfo(
    module_slug='calendar',
    badge_fn=_calendar_badge,
))
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest workspace/calendar/tests/test_calendar.py::CalendarBadgeProviderTests -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add workspace/calendar/apps.py workspace/calendar/tests/test_calendar.py
git commit -m "feat(calendar): register badge provider for upcoming events count"
```

---

### Task 4: Register badge provider for Mail module

**Files:**
- Modify: `workspace/mail/apps.py:1-33`
- Test: `workspace/mail/tests.py` (create)

**Step 1: Write the failing test**

Create `workspace/mail/tests.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class MailBadgeProviderTests(TestCase):
    """Tests for the mail badge provider."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mailuser', email='mail@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='mail@test.com',
            imap_host='imap.test.com',
            smtp_host='smtp.test.com',
            username='mail@test.com',
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type=MailFolder.FolderType.INBOX,
        )

    def test_badge_returns_unread_email_count(self):
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Hello', is_read=False,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=2, subject='World', is_read=False,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=3, subject='Read', is_read=True,
        )

        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.user)
        self.assertEqual(counts.get('mail'), 2)

    def test_badge_returns_zero_when_all_read(self):
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Read', is_read=True,
        )

        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.user)
        self.assertEqual(counts.get('mail'), 0)

    def test_badge_excludes_deleted_messages(self):
        from django.utils import timezone
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Deleted', is_read=False,
            deleted_at=timezone.now(),
        )

        from workspace.core.module_registry import registry
        counts = registry.get_badge_counts(self.user)
        self.assertEqual(counts.get('mail'), 0)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest workspace/mail/tests.py -v`
Expected: FAIL — `'mail'` not in counts

**Step 3: Register the badge provider in mail/apps.py**

In `workspace/mail/apps.py`, inside the `ready()` method, add after the last `register_search_provider` call:

```python
from workspace.core.module_registry import BadgeProviderInfo

def _mail_badge(user):
    from workspace.mail.models import MailMessage
    return MailMessage.objects.filter(
        account__owner=user,
        is_read=False,
        deleted_at__isnull=True,
    ).count()

registry.register_badge_provider(BadgeProviderInfo(
    module_slug='mail',
    badge_fn=_mail_badge,
))
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest workspace/mail/tests.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add workspace/mail/apps.py workspace/mail/tests.py
git commit -m "feat(mail): register badge provider for unread email count"
```

---

### Task 5: Integrate badge counts into dashboard view

**Files:**
- Modify: `workspace/dashboard/views.py:161-189` (`_build_dashboard_context`)

**Step 1: Write the failing test**

Replace `workspace/dashboard/tests.py` with:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from unittest.mock import patch

from workspace.dashboard.views import _build_dashboard_context

User = get_user_model()


class DashboardContextTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='dashuser', email='dash@test.com', password='pass123',
        )

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_module_badges(self, mock_registry):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_badge_counts.return_value = {'chat': 5, 'calendar': 2}

        context = _build_dashboard_context(self.user)
        self.assertEqual(context['module_badges'], {'chat': 5, 'calendar': 2})

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_empty_badges_when_no_providers(self, mock_registry):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_badge_counts.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertEqual(context['module_badges'], {})
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest workspace/dashboard/tests.py -v`
Expected: FAIL — `KeyError: 'module_badges'`

**Step 3: Add badge counts to _build_dashboard_context**

In `workspace/dashboard/views.py`, inside `_build_dashboard_context()`, add after the `context = { 'modules': ... }` line (line 170-172):

```python
context['module_badges'] = registry.get_badge_counts(user)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest workspace/dashboard/tests.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add workspace/dashboard/views.py workspace/dashboard/tests.py
git commit -m "feat(dashboard): integrate badge counts into dashboard context"
```

---

### Task 6: Redesign modules grid template as compact dock with badges

**Files:**
- Modify: `workspace/dashboard/templates/dashboard/partials/modules_grid.html`

**Step 1: Replace the modules grid template**

Replace the entire content of `workspace/dashboard/templates/dashboard/partials/modules_grid.html` with:

```html
<div class="flex flex-wrap justify-center gap-3">
  {% for module in modules %}
    {% if module.active %}
      <a
        href="{{ module.url }}"
        class="group relative flex flex-col items-center gap-1.5 p-3 rounded-xl hover:bg-base-200 transition-colors w-20"
      >
        <div class="relative">
          <div class="w-12 h-12 rounded-xl bg-{{ module.color }}/10 flex items-center justify-center text-{{ module.color }} group-hover:bg-{{ module.color }}/20 transition-colors">
            <i data-lucide="{{ module.icon }}" class="w-6 h-6"></i>
          </div>
          {% with badge=module_badges|default_if_none:"" %}
            {% if badge and module.slug in badge and badge|lookup:module.slug %}
              <span class="absolute -top-1.5 -right-1.5 min-w-5 h-5 flex items-center justify-center rounded-full bg-{{ module.color }} text-{{ module.color }}-content text-xs font-bold px-1">
                {{ badge|lookup:module.slug }}
              </span>
            {% endif %}
          {% endwith %}
        </div>
        <span class="text-xs font-medium text-base-content text-center leading-tight">{{ module.name }}</span>
      </a>
    {% else %}
      <div class="relative flex flex-col items-center gap-1.5 p-3 rounded-xl opacity-50 w-20">
        <div class="w-12 h-12 rounded-xl bg-base-200 flex items-center justify-center text-base-content/40 border border-dashed border-base-300">
          <i data-lucide="{{ module.icon }}" class="w-6 h-6"></i>
        </div>
        <span class="text-xs font-medium text-base-content/50 text-center leading-tight">{{ module.name }}</span>
      </div>
    {% endif %}
  {% endfor %}
</div>
```

**Important:** The template uses a `lookup` filter to access dict values by key. We need to check if this filter exists. Django does not have a built-in `lookup` filter, so we need a different approach.

**Step 2: Instead, pass badge count directly on each module dict**

The cleaner approach is to merge badge counts into the module dicts in the view. In `workspace/dashboard/views.py`, modify `_build_dashboard_context()`:

Replace:
```python
context = {
    'modules': [m for m in registry.get_for_template() if m['slug'] != 'dashboard'],
}
```

With:
```python
badge_counts = registry.get_badge_counts(user)
modules = []
for m in registry.get_for_template():
    if m['slug'] != 'dashboard':
        m['badge_count'] = badge_counts.get(m['slug'], 0)
        modules.append(m)
context = {
    'modules': modules,
    'module_badges': badge_counts,
}
```

Then the template becomes simpler — use `module.badge_count` directly:

```html
<div class="flex flex-wrap justify-center gap-3">
  {% for module in modules %}
    {% if module.active %}
      <a
        href="{{ module.url }}"
        class="group relative flex flex-col items-center gap-1.5 p-3 rounded-xl hover:bg-base-200 transition-colors w-20"
      >
        <div class="relative">
          <div class="w-12 h-12 rounded-xl bg-{{ module.color }}/10 flex items-center justify-center text-{{ module.color }} group-hover:bg-{{ module.color }}/20 transition-colors">
            <i data-lucide="{{ module.icon }}" class="w-6 h-6"></i>
          </div>
          {% if module.badge_count %}
            <span class="absolute -top-1.5 -right-1.5 min-w-5 h-5 flex items-center justify-center rounded-full bg-{{ module.color }} text-{{ module.color }}-content text-xs font-bold px-1">
              {{ module.badge_count }}
            </span>
          {% endif %}
        </div>
        <span class="text-xs font-medium text-base-content text-center leading-tight">{{ module.name }}</span>
      </a>
    {% else %}
      <div class="relative flex flex-col items-center gap-1.5 p-3 rounded-xl opacity-50 w-20">
        <div class="w-12 h-12 rounded-xl bg-base-200 flex items-center justify-center text-base-content/40 border border-dashed border-base-300">
          <i data-lucide="{{ module.icon }}" class="w-6 h-6"></i>
        </div>
        <span class="text-xs font-medium text-base-content/50 text-center leading-tight">{{ module.name }}</span>
      </div>
    {% endif %}
  {% endfor %}
</div>
```

**Step 3: Manually verify in browser**

Run: `python manage.py runserver`
Navigate to `/` (dashboard)
Expected:
- Modules displayed as compact dock tiles (icon + name, no description)
- Chat tile shows unread message count badge
- Calendar tile shows upcoming events count badge
- Mail tile shows unread emails count badge
- Badges only appear when count > 0
- Inactive modules (Notes, Tasks) show with dashed border, no badge

**Step 4: Commit**

```bash
git add workspace/dashboard/views.py workspace/dashboard/templates/dashboard/partials/modules_grid.html
git commit -m "feat(dashboard): redesign modules grid as compact dock with badge counts"
```

---

### Task 7: Run full test suite and fix any issues

**Step 1: Run all tests**

Run: `python -m pytest workspace/ -v`
Expected: All tests PASS

**Step 2: Fix any failures**

If any existing tests break due to the new `badge_count` key on modules or the changed context, update them.

**Step 3: Final commit if fixes were needed**

```bash
git add -u
git commit -m "fix(dashboard): fix test compatibility after app grid changes"
```
