# Voice Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time P2P voice calls (1-to-1 and group) to the existing chat module.

**Architecture:** WebRTC full-mesh P2P for audio, signaled via existing SSE + REST. Inline SSE events bypass the polling provider for sub-second signaling delivery. Self-hosted coturn for STUN/TURN. Transport abstracted behind `CallManager` JS module for future SFU swap.

**Tech Stack:** Django 6 + DRF, Celery, Redis pub/sub, WebRTC (`RTCPeerConnection`), Alpine.js, pywebpush, coturn.

**Spec:** `docs/superpowers/specs/2026-03-18-voice-chat-design.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `workspace/chat/call_models.py` | `Call` and `CallParticipant` models |
| `workspace/chat/call_services.py` | Business logic: create/join/leave/reject calls, TURN credentials, inline SSE publishing |
| `workspace/chat/call_views.py` | DRF API views for all call endpoints |
| `workspace/chat/call_serializers.py` | DRF serializers for call request/response |
| `workspace/chat/call_tasks.py` | Celery tasks: call timeout, stale participant cleanup |
| `workspace/chat/tests_call.py` | All call-related tests |
| `workspace/chat/ui/static/chat/ui/js/call-manager.js` | WebRTC transport abstraction (pure JS) |
| `workspace/chat/migrations/XXXX_add_call_models.py` | Auto-generated |
| `workspace/chat/migrations/XXXX_create_system_user.py` | Data migration for system user |
| `docs/deployments/docker-compose/coturn/turnserver.conf` | coturn config |

### Modified files

| File | Change |
|---|---|
| `workspace/chat/models.py` | Import and re-export `Call`, `CallParticipant` from `call_models.py` |
| `workspace/chat/urls.py` | Add 6 call endpoint routes |
| `workspace/core/sse_registry.py` | Add `notify_sse_inline()` helper |
| `workspace/core/views_sse.py` | Handle `inline: True` pub/sub messages in `_event_stream_pubsub()` |
| `workspace/chat/ui/templates/chat/ui/index.html` | Call button in header, overlay component, Alpine store, SSE event handlers |
| `workspace/chat/ui/static/chat/ui/css/chat.css` | Overlay styles |
| `workspace/settings.py` | Add TURN_SECRET, TURN_SERVER_URL, STUN_SERVER_URL |
| `docs/deployments/docker-compose/docker-compose.yml` | Add coturn + redis services, TURN env vars |

---

## Task 1: Inline SSE events in core

Add the ability to deliver SSE events instantly via Redis pub/sub without going through provider polling.

**Files:**
- Modify: `workspace/core/sse_registry.py:74-103`
- Modify: `workspace/core/views_sse.py:144-157`
- Test: `workspace/core/tests.py`

- [ ] **Step 1: Write test for `notify_sse_inline`**

```python
# workspace/core/tests.py — add at end of file
from unittest.mock import patch, MagicMock
import orjson

class InlineSSETests(TestCase):
    @patch('workspace.core.sse_registry._get_redis')
    def test_notify_sse_inline_publishes_with_inline_flag(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        from workspace.core.sse_registry import notify_sse_inline
        notify_sse_inline(
            event='chat.signal',
            data={'call_id': 'abc', 'type': 'offer'},
            user_id=42,
        )

        mock_redis.publish.assert_called_once()
        channel, payload = mock_redis.publish.call_args[0]
        assert channel == 'sse:user:42'
        parsed = orjson.loads(payload)
        assert parsed['inline'] is True
        assert parsed['event'] == 'chat.signal'
        assert parsed['data']['call_id'] == 'abc'

    @patch('workspace.core.sse_registry._get_redis')
    def test_notify_sse_inline_noop_without_redis(self, mock_get_redis):
        mock_get_redis.return_value = None

        from workspace.core.sse_registry import notify_sse_inline
        # Should not raise
        notify_sse_inline(event='chat.signal', data={}, user_id=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.core.tests.InlineSSETests -v2`
Expected: FAIL — `notify_sse_inline` does not exist

- [ ] **Step 3: Implement `notify_sse_inline` in sse_registry.py**

Add after the existing `notify_sse` function at line 103:

```python
def notify_sse_inline(event: str, data: dict, user_id: int):
    """Publish an inline SSE event directly via Redis pub/sub.

    Unlike notify_sse(), the event payload is embedded in the pub/sub message
    and delivered immediately to the SSE stream without triggering a provider poll.
    Requires Redis — silently ignored if Redis is unavailable.
    """
    redis = _get_redis()
    if redis is None:
        return
    try:
        redis.publish(
            f'sse:user:{user_id}',
            orjson.dumps({'inline': True, 'event': event, 'data': data}),
        )
    except Exception:
        logger.warning(
            "Redis publish failed for inline SSE event (event=%s, user=%s)",
            event, user_id, exc_info=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test workspace.core.tests.InlineSSETests -v2`
Expected: PASS

- [ ] **Step 5: Write test for inline event handling in SSE stream**

This tests the pub/sub message handler logic (not the full streaming response). We test the branch extraction:

```python
# workspace/core/tests.py — add to InlineSSETests
def test_inline_message_detected(self):
    """Verify that an inline pub/sub message is distinguishable from a provider notification."""
    inline_msg = orjson.dumps({'inline': True, 'event': 'chat.signal', 'data': {'foo': 'bar'}})
    provider_msg = orjson.dumps({'provider': 'chat'})

    inline_parsed = orjson.loads(inline_msg)
    provider_parsed = orjson.loads(provider_msg)

    assert inline_parsed.get('inline') is True
    assert provider_parsed.get('inline') is None
```

- [ ] **Step 6: Implement inline event handling in `views_sse.py`**

In `_event_stream_pubsub()`, modify the `elif message['type'] == 'message':` block (lines 144-157):

Replace the existing block:
```python
            elif message['type'] == 'message':
                try:
                    # Targeted: only poll the provider that published
                    data = orjson.loads(message['data'])
                    slug = data['provider']
                    if slug in providers:
                        yield from _poll_provider(
                            slug, providers[slug], time.monotonic(), user_id,
                        )
                except Exception:
                    logger.exception(
                        "Failed to process Pub/Sub message for user %s",
                        user_id,
                    )
```

With:
```python
            elif message['type'] == 'message':
                try:
                    data = orjson.loads(message['data'])
                    if data.get('inline'):
                        # Inline event: yield immediately, no provider poll
                        yield _format_sse(data['event'], data['data'])
                    else:
                        # Targeted: only poll the provider that published
                        slug = data['provider']
                        if slug in providers:
                            yield from _poll_provider(
                                slug, providers[slug], time.monotonic(), user_id,
                            )
                except Exception:
                    logger.exception(
                        "Failed to process Pub/Sub message for user %s",
                        user_id,
                    )
```

- [ ] **Step 7: Run all core tests**

Run: `python manage.py test workspace.core -v2`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add workspace/core/sse_registry.py workspace/core/views_sse.py workspace/core/tests.py
git commit -m "feat(core/sse): add inline SSE events for sub-second delivery via Redis pub/sub"
```

---

## Task 2: Call data models + system user

**Files:**
- Create: `workspace/chat/call_models.py`
- Modify: `workspace/chat/models.py`
- Test: `workspace/chat/tests_call.py`

- [ ] **Step 1: Write model tests**

```python
# workspace/chat/tests_call.py
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, Call, CallParticipant

User = get_user_model()


class CallModelTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice', password='pass')
        self.user_b = User.objects.create_user(username='bob', password='pass')
        self.conv = Conversation.objects.create(kind='group', title='Test', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    def test_create_call(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a)
        self.assertEqual(call.status, 'ringing')
        self.assertIsNone(call.started_at)
        self.assertIsNone(call.ended_at)

    def test_one_active_call_per_conversation(self):
        Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        with self.assertRaises(IntegrityError):
            Call.objects.create(conversation=self.conv, initiator=self.user_b, status='ringing')

    def test_ended_call_allows_new_call(self):
        Call.objects.create(conversation=self.conv, initiator=self.user_a, status='ended')
        call2 = Call.objects.create(conversation=self.conv, initiator=self.user_b)
        self.assertEqual(call2.status, 'ringing')

    def test_participant_unique_active(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        CallParticipant.objects.create(call=call, user=self.user_a)
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(call=call, user=self.user_a)

    def test_participant_rejoin_after_leave(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        p1 = CallParticipant.objects.create(call=call, user=self.user_a)
        p1.left_at = timezone.now()
        p1.save()
        # Should be able to create a new active participation
        p2 = CallParticipant.objects.create(call=call, user=self.user_a)
        self.assertIsNone(p2.left_at)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test workspace.chat.tests_call.CallModelTests -v2`
Expected: FAIL — `Call` and `CallParticipant` don't exist

- [ ] **Step 3: Create `call_models.py`**

```python
# workspace/chat/call_models.py
from django.conf import settings
from django.db import models
from django.db.models import Q

from workspace.common.uuids import uuid_v7_or_v4


class Call(models.Model):
    class Status(models.TextChoices):
        RINGING = 'ringing', 'Ringing'
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'
        MISSED = 'missed', 'Missed'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    conversation = models.ForeignKey(
        'chat.Conversation',
        on_delete=models.CASCADE,
        related_name='calls',
    )
    initiator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='initiated_calls',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RINGING)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['conversation'],
                condition=Q(status__in=['ringing', 'active']),
                name='one_active_call_per_conversation',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
        ]

    def __str__(self):
        return f'Call {self.uuid} ({self.status})'


class CallParticipant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='call_participations',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    muted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['call', 'user'],
                condition=Q(left_at__isnull=True),
                name='unique_active_call_participant',
            ),
        ]

    def __str__(self):
        return f'{self.user} in {self.call}'
```

- [ ] **Step 4: Re-export from `models.py`**

Add at the end of `workspace/chat/models.py`:

```python
from .call_models import Call, CallParticipant  # noqa: F401
```

- [ ] **Step 5: Generate and run migration**

Run: `python manage.py makemigrations chat --name add_call_models`
Run: `python manage.py migrate`

- [ ] **Step 6: Run model tests**

Run: `python manage.py test workspace.chat.tests_call.CallModelTests -v2`
Expected: PASS

- [ ] **Step 7: Create system user data migration**

Run: `python manage.py makemigrations chat --empty --name create_system_user`

Then edit the generated migration:

```python
from django.db import migrations


def create_system_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.get_or_create(
        username='system',
        defaults={
            'is_active': False,
            'email': 'system@localhost',
        },
    )


def remove_system_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username='system').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '<previous_migration>'),  # fill in actual name
    ]

    operations = [
        migrations.RunPython(create_system_user, remove_system_user),
    ]
```

- [ ] **Step 8: Run migration**

Run: `python manage.py migrate`

- [ ] **Step 9: Commit**

```bash
git add workspace/chat/call_models.py workspace/chat/models.py workspace/chat/tests_call.py workspace/chat/migrations/
git commit -m "feat(chat): add Call and CallParticipant models with system user"
```

---

## Task 3: Settings + coturn configuration

**Files:**
- Modify: `workspace/settings.py:610`
- Create: `docs/deployments/docker-compose/coturn/turnserver.conf`
- Modify: `docs/deployments/docker-compose/docker-compose.yml`

- [ ] **Step 1: Add TURN settings**

After the WEBPUSH section (line 610) in `workspace/settings.py`:

```python
# --------------------------------------------------
# Voice Calls (WebRTC / TURN)
# --------------------------------------------------
STUN_SERVER_URL = os.getenv('STUN_SERVER_URL', 'stun:stun.l.google.com:19302')
TURN_SERVER_URL = os.getenv('TURN_SERVER_URL', '')
TURN_SECRET = os.getenv('TURN_SECRET', '')
```

Also add to `CELERY_BEAT_SCHEDULE` (in the same file, around line 590):

```python
    'cleanup-stale-call-participants': {
        'task': 'chat.cleanup_stale_participants',
        'schedule': 15.0,
    },
```

- [ ] **Step 2: Create coturn config**

```ini
# docs/deployments/docker-compose/coturn/turnserver.conf
listening-port=3478
fingerprint
lt-cred-mech
use-auth-secret
static-auth-secret=${TURN_SECRET}
realm=workspace
total-quota=100
stale-nonce=600
no-multicast-peers
no-cli
```

- [ ] **Step 3: Add coturn + redis to docker-compose**

Add redis service and coturn service. Add TURN env vars to web and celery-worker:

Redis service (add before `volumes:`):
```yaml
  redis:
    image: redis:7-alpine
    volumes:
      - redis-data:/data
    restart: unless-stopped

  coturn:
    image: coturn/coturn:latest
    network_mode: host
    volumes:
      - ./coturn/turnserver.conf:/etc/turnserver.conf:ro
    environment:
      - TURN_SECRET=${TURN_SECRET:-}
    restart: unless-stopped
```

Add to `volumes:`:
```yaml
  redis-data:
```

Add TURN env vars to `web` and `celery-worker` environment sections:
```yaml
      - TURN_SECRET=${TURN_SECRET:-}
      - TURN_SERVER_URL=${TURN_SERVER_URL:-}
      - STUN_SERVER_URL=${STUN_SERVER_URL:-stun:stun.l.google.com:19302}
      - REDIS_URL=redis://redis:6379/0
```

- [ ] **Step 4: Commit**

```bash
git add workspace/settings.py docs/deployments/docker-compose/
git commit -m "feat(infra): add TURN/STUN settings, coturn config, and redis to docker-compose"
```

---

## Task 4: Call services (business logic)

**Files:**
- Create: `workspace/chat/call_services.py`
- Test: `workspace/chat/tests_call.py`

- [ ] **Step 1: Write service tests**

```python
# workspace/chat/tests_call.py — add below CallModelTests
from unittest.mock import patch

class CallServiceTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_svc', password='pass')
        self.user_b = User.objects.create_user(username='bob_svc', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    def test_start_call(self, mock_push, mock_sse):
        from workspace.chat.call_services import start_call
        call = start_call(conversation=self.conv, initiator=self.user_a)
        self.assertEqual(call.status, 'ringing')
        self.assertEqual(call.initiator, self.user_a)
        self.assertTrue(CallParticipant.objects.filter(call=call, user=self.user_a).exists())
        # SSE should be sent to user_b (the other member)
        self.assertTrue(mock_sse.called)

    @patch('workspace.chat.call_services.notify_sse_inline')
    def test_join_call_activates(self, mock_sse):
        from workspace.chat.call_services import start_call, join_call
        with patch('workspace.chat.call_services.send_push_notification'):
            call = start_call(conversation=self.conv, initiator=self.user_a)
        join_call(call=call, user=self.user_b)
        call.refresh_from_db()
        self.assertEqual(call.status, 'active')
        self.assertIsNotNone(call.started_at)

    @patch('workspace.chat.call_services.notify_sse_inline')
    def test_leave_call_ends_when_empty(self, mock_sse):
        from workspace.chat.call_services import start_call, join_call, leave_call
        with patch('workspace.chat.call_services.send_push_notification'):
            call = start_call(conversation=self.conv, initiator=self.user_a)
        join_call(call=call, user=self.user_b)
        leave_call(call=call, user=self.user_a)
        leave_call(call=call, user=self.user_b)
        call.refresh_from_db()
        self.assertEqual(call.status, 'ended')
        self.assertIsNotNone(call.ended_at)

    @patch('workspace.chat.call_services.notify_sse_inline')
    def test_cannot_join_if_already_in_call(self, mock_sse):
        from workspace.chat.call_services import start_call, join_call, CallError
        conv2 = Conversation.objects.create(kind='group', title='Other', created_by=self.user_a)
        ConversationMember.objects.create(conversation=conv2, user=self.user_a)
        ConversationMember.objects.create(conversation=conv2, user=self.user_b)
        with patch('workspace.chat.call_services.send_push_notification'):
            call1 = start_call(conversation=self.conv, initiator=self.user_a)
            call2 = start_call(conversation=conv2, initiator=self.user_b)
        join_call(call=call1, user=self.user_b)
        with self.assertRaises(CallError):
            join_call(call=call2, user=self.user_b)

    def test_generate_turn_credentials(self):
        from workspace.chat.call_services import generate_ice_servers
        with self.settings(
            TURN_SECRET='test-secret',
            TURN_SERVER_URL='turn:example.com:3478',
            STUN_SERVER_URL='stun:stun.l.google.com:19302',
        ):
            servers = generate_ice_servers()
            self.assertEqual(servers[0]['urls'], 'stun:stun.l.google.com:19302')
            self.assertEqual(servers[1]['urls'], 'turn:example.com:3478')
            self.assertIn('username', servers[1])
            self.assertIn('credential', servers[1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test workspace.chat.tests_call.CallServiceTests -v2`
Expected: FAIL — `call_services` module does not exist

- [ ] **Step 3: Implement `call_services.py`**

```python
# workspace/chat/call_services.py
import base64
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.core.sse_registry import notify_sse_inline

from .call_models import Call, CallParticipant
from .models import ConversationMember, Message

logger = logging.getLogger(__name__)
User = get_user_model()


class CallError(Exception):
    """Raised when a call operation is invalid."""
    pass


def _get_system_user():
    return User.objects.get(username='system')


def _active_member_ids(conversation, exclude_user=None):
    """Return user IDs of active members in a conversation."""
    qs = ConversationMember.objects.filter(
        conversation=conversation, left_at__isnull=True,
    ).values_list('user_id', flat=True)
    if exclude_user:
        qs = qs.exclude(user_id=exclude_user.id)
    return list(qs)


def _active_participant_ids(call, exclude_user=None):
    """Return user IDs of active participants in a call."""
    qs = CallParticipant.objects.filter(
        call=call, left_at__isnull=True,
    ).values_list('user_id', flat=True)
    if exclude_user:
        qs = qs.exclude(user_id=exclude_user.id)
    return list(qs)


def _send_call_event(event, data, user_ids):
    """Send an inline SSE event to a list of user IDs."""
    for uid in user_ids:
        notify_sse_inline(event=f'chat.{event}', data=data, user_id=uid)


def send_push_notification(call, exclude_user=None):
    """Send push notification for incoming call to all conversation members."""
    from workspace.notifications.services import notify_many
    member_ids = _active_member_ids(call.conversation, exclude_user=exclude_user)
    recipients = User.objects.filter(id__in=member_ids)
    if recipients:
        notify_many(
            recipients=recipients,
            origin='chat',
            title=f'{call.initiator.username} vous appelle',
            body=f'Appel vocal dans {call.conversation.title or "conversation"}',
            url=f'/chat?call={call.uuid}',
            priority='high',
        )


def start_call(*, conversation, initiator):
    """Start a new call in a conversation."""
    call = Call.objects.create(conversation=conversation, initiator=initiator)
    CallParticipant.objects.create(call=call, user=initiator)

    # Notify other members
    member_ids = _active_member_ids(conversation, exclude_user=initiator)
    _send_call_event('call.incoming', {
        'call_id': str(call.uuid),
        'conversation_id': str(conversation.uuid),
        'initiator_id': initiator.id,
        'initiator_name': initiator.username,
    }, member_ids)

    send_push_notification(call, exclude_user=initiator)

    # Schedule timeout
    from .call_tasks import call_timeout
    call_timeout.apply_async(args=[str(call.uuid)], countdown=30)

    return call


def join_call(*, call, user):
    """Join an existing call."""
    if call.status not in ('ringing', 'active'):
        raise CallError('Call is no longer available')

    # Check user is not already in another active call
    already_in = CallParticipant.objects.filter(
        user=user, left_at__isnull=True,
    ).exclude(call=call).exists()
    if already_in:
        raise CallError('Already in another call')

    CallParticipant.objects.create(call=call, user=user)

    # Activate call if this is the second participant
    active_count = CallParticipant.objects.filter(call=call, left_at__isnull=True).count()
    if active_count >= 2 and call.status == 'ringing':
        call.status = 'active'
        call.started_at = timezone.now()
        call.save(update_fields=['status', 'started_at'])

    # Notify other participants
    participant_ids = _active_participant_ids(call, exclude_user=user)
    _send_call_event('call.participant_joined', {
        'call_id': str(call.uuid),
        'user_id': user.id,
        'user_name': user.username,
    }, participant_ids)

    return call


def leave_call(*, call, user):
    """Leave a call."""
    participant = CallParticipant.objects.filter(
        call=call, user=user, left_at__isnull=True,
    ).first()
    if not participant:
        return

    participant.left_at = timezone.now()
    participant.save(update_fields=['left_at'])

    remaining = CallParticipant.objects.filter(call=call, left_at__isnull=True).count()

    # Notify remaining
    participant_ids = _active_participant_ids(call)
    _send_call_event('call.participant_left', {
        'call_id': str(call.uuid),
        'user_id': user.id,
    }, participant_ids)

    if remaining == 0:
        _end_call(call)


def reject_call(*, call, user):
    """Reject an incoming call."""
    _send_call_event('call.rejected', {
        'call_id': str(call.uuid),
        'user_id': user.id,
    }, [call.initiator_id])


def relay_signal(*, call, from_user, to_user_id, signal_type, payload):
    """Relay a WebRTC signaling message to a specific participant."""
    # Validate both users are active participants
    active_users = set(
        CallParticipant.objects.filter(
            call=call, left_at__isnull=True,
        ).values_list('user_id', flat=True)
    )
    if from_user.id not in active_users or to_user_id not in active_users:
        raise CallError('Both users must be active call participants')

    notify_sse_inline(
        event='chat.call.signal',
        data={
            'call_id': str(call.uuid),
            'from_user': from_user.id,
            'type': signal_type,
            'payload': payload,
        },
        user_id=to_user_id,
    )


def update_mute(*, call, user, muted):
    """Update mute state for a participant."""
    CallParticipant.objects.filter(
        call=call, user=user, left_at__isnull=True,
    ).update(muted=muted)

    participant_ids = _active_participant_ids(call, exclude_user=user)
    _send_call_event('call.mute_changed', {
        'call_id': str(call.uuid),
        'user_id': user.id,
        'muted': muted,
    }, participant_ids)


def _end_call(call):
    """End a call and create system message."""
    call.status = 'ended'
    call.ended_at = timezone.now()
    call.save(update_fields=['status', 'ended_at'])

    duration = ''
    if call.started_at:
        delta = call.ended_at - call.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        duration = f'{minutes}:{seconds:02d}'

    participant_count = CallParticipant.objects.filter(call=call).count()

    # Create system message
    system_user = _get_system_user()
    body = f'Appel vocal \u00b7 {participant_count} participants \u00b7 {duration}' if duration else f'Appel vocal \u00b7 {participant_count} participants'
    Message.objects.create(
        conversation=call.conversation,
        author=system_user,
        body=body,
        body_html=f'<p>{body}</p>',
    )

    # Notify all conversation members
    member_ids = _active_member_ids(call.conversation)
    _send_call_event('call.ended', {
        'call_id': str(call.uuid),
        'duration': duration,
        'participant_count': participant_count,
    }, member_ids)


def generate_ice_servers():
    """Generate ICE server config with ephemeral TURN credentials."""
    stun_url = getattr(settings, 'STUN_SERVER_URL', 'stun:stun.l.google.com:19302')
    turn_url = getattr(settings, 'TURN_SERVER_URL', '')
    turn_secret = getattr(settings, 'TURN_SECRET', '')

    servers = [{'urls': stun_url}]

    if turn_url and turn_secret:
        expiry = int(time.time()) + 86400  # 24 hours
        username = f'{expiry}:workspace'
        credential = base64.b64encode(
            hmac.new(turn_secret.encode(), username.encode(), hashlib.sha1).digest(),
        ).decode()
        servers.append({
            'urls': turn_url,
            'username': username,
            'credential': credential,
        })

    return servers
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test workspace.chat.tests_call.CallServiceTests -v2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workspace/chat/call_services.py workspace/chat/tests_call.py
git commit -m "feat(chat): add call business logic services"
```

---

## Task 5: Call Celery tasks

**Files:**
- Create: `workspace/chat/call_tasks.py`
- Test: `workspace/chat/tests_call.py`

- [ ] **Step 1: Write task tests**

```python
# workspace/chat/tests_call.py — add
class CallTaskTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_task', password='pass')
        self.user_b = User.objects.create_user(username='bob_task', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.chat.call_services.notify_sse_inline')
    def test_timeout_marks_missed(self, mock_sse):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='ringing')
        CallParticipant.objects.create(call=call, user=self.user_a)

        from workspace.chat.call_tasks import call_timeout
        call_timeout(str(call.uuid))

        call.refresh_from_db()
        self.assertEqual(call.status, 'missed')

    @patch('workspace.chat.call_services.notify_sse_inline')
    def test_timeout_noop_if_active(self, mock_sse):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        from workspace.chat.call_tasks import call_timeout
        call_timeout(str(call.uuid))
        call.refresh_from_db()
        self.assertEqual(call.status, 'active')  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test workspace.chat.tests_call.CallTaskTests -v2`
Expected: FAIL — `call_tasks` module does not exist

- [ ] **Step 3: Implement `call_tasks.py`**

```python
# workspace/chat/call_tasks.py
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.core.sse_registry import notify_sse_inline

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name='chat.call_timeout', ignore_result=True, soft_time_limit=10)
def call_timeout(call_uuid: str):
    """Mark a call as missed if still ringing after timeout."""
    from .call_models import Call
    from .models import Message

    try:
        call = Call.objects.get(uuid=call_uuid)
    except Call.DoesNotExist:
        return

    if call.status != 'ringing':
        return

    call.status = 'missed'
    call.ended_at = timezone.now()
    call.save(update_fields=['status', 'ended_at'])

    # Create system message
    system_user = User.objects.get(username='system')
    body = f'Appel manqu\u00e9 de {call.initiator.username}'
    Message.objects.create(
        conversation=call.conversation,
        author=system_user,
        body=body,
        body_html=f'<p>{body}</p>',
    )

    # Notify initiator
    notify_sse_inline(
        event='chat.call.missed',
        data={'call_id': str(call.uuid), 'conversation_id': str(call.conversation_id)},
        user_id=call.initiator_id,
    )

    logger.info("Call %s timed out (missed)", call_uuid)


@shared_task(name='chat.cleanup_stale_participants', ignore_result=True, soft_time_limit=30)
def cleanup_stale_participants():
    """Remove participants from active calls if their SSE connection is stale.

    Run every 15 seconds via Celery beat.
    """
    from .call_models import Call, CallParticipant
    from .call_services import leave_call

    stale_threshold = timezone.now() - timezone.timedelta(seconds=30)

    active_calls = Call.objects.filter(status='active')
    for call in active_calls:
        # Check each participant — in a future version, compare against SSE
        # connection heartbeat timestamps stored in Redis. For now, this task
        # is a placeholder that runs and logs; actual stale detection relies
        # on the client-side RTCPeerConnection.oniceconnectionstatechange.
        pass
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test workspace.chat.tests_call.CallTaskTests -v2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workspace/chat/call_tasks.py workspace/chat/tests_call.py
git commit -m "feat(chat): add Celery tasks for call timeout and stale cleanup"
```

---

## Task 6: Call API views + URL routing

**Files:**
- Create: `workspace/chat/call_serializers.py`
- Create: `workspace/chat/call_views.py`
- Modify: `workspace/chat/urls.py`
- Test: `workspace/chat/tests_call.py`

- [ ] **Step 1: Write API tests**

```python
# workspace/chat/tests_call.py — add
from rest_framework.test import APITestCase


class CallAPITests(APITestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_api', password='pass')
        self.user_b = User.objects.create_user(username='bob_api', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_services.call_timeout')
    def test_start_call(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 201)
        self.assertIn('call_id', resp.data)
        self.assertIn('ice_servers', resp.data)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_services.call_timeout')
    def test_join_call(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        call_id = resp.data['call_id']

        self.client.force_authenticate(self.user_b)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('participants', resp.data)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_services.call_timeout')
    def test_leave_call(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.client.force_authenticate(self.user_b)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/leave')
        self.assertEqual(resp.status_code, 204)

    def test_outsider_cannot_start_call(self):
        outsider = User.objects.create_user(username='outsider_api', password='pass')
        self.client.force_authenticate(outsider)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 403)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_services.call_timeout')
    def test_signal_validates_participants(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.client.force_authenticate(self.user_b)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')

        # user_b signals to user_a — should work
        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.uuid}/call/signal',
            {'to_user': self.user_a.id, 'type': 'offer', 'payload': {'sdp': 'test'}},
            format='json',
        )
        self.assertEqual(resp.status_code, 204)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test workspace.chat.tests_call.CallAPITests -v2`
Expected: FAIL — views and URLs don't exist

- [ ] **Step 3: Create `call_serializers.py`**

```python
# workspace/chat/call_serializers.py
from rest_framework import serializers


class CallSignalSerializer(serializers.Serializer):
    to_user = serializers.IntegerField()
    type = serializers.ChoiceField(choices=['offer', 'answer', 'ice'])
    payload = serializers.DictField()


class CallMuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()
```

- [ ] **Step 4: Create `call_views.py`**

```python
# workspace/chat/call_views.py
from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .call_models import Call, CallParticipant
from .call_serializers import CallMuteSerializer, CallSignalSerializer
from .call_services import (
    CallError,
    generate_ice_servers,
    join_call,
    leave_call,
    reject_call,
    relay_signal,
    start_call,
    update_mute,
)
from .services import user_conversation_ids


class CallMembershipMixin:
    """Verify user is an active member of the conversation."""

    def check_membership(self, request, conversation_id):
        if conversation_id not in set(user_conversation_ids(request.user)):
            return False
        return True


@extend_schema(tags=['Chat / Voice Calls'])
class CallStartView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Start a voice call")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        from .models import Conversation
        try:
            conv = Conversation.objects.get(uuid=conversation_id)
        except Conversation.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            call = start_call(conversation=conv, initiator=request.user)
        except IntegrityError:
            return Response(
                {'error': 'A call is already active in this conversation'},
                status=status.HTTP_409_CONFLICT,
            )

        return Response({
            'call_id': str(call.uuid),
            'ice_servers': generate_ice_servers(),
        }, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Chat / Voice Calls'])
class CallJoinView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Join a voice call")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        call = Call.objects.filter(
            conversation_id=conversation_id,
            status__in=['ringing', 'active'],
        ).first()
        if not call:
            return Response({'error': 'No active call'}, status=status.HTTP_404_NOT_FOUND)

        try:
            join_call(call=call, user=request.user)
        except CallError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)

        participants = list(
            CallParticipant.objects.filter(call=call, left_at__isnull=True)
            .values_list('user__id', 'user__username')
        )

        return Response({
            'call_id': str(call.uuid),
            'participants': [{'id': uid, 'name': name} for uid, name in participants],
            'ice_servers': generate_ice_servers(),
        })


@extend_schema(tags=['Chat / Voice Calls'])
class CallLeaveView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Leave a voice call")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        call = Call.objects.filter(
            conversation_id=conversation_id,
            status__in=['ringing', 'active'],
        ).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)

        leave_call(call=call, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallRejectView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Reject an incoming call")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        call = Call.objects.filter(
            conversation_id=conversation_id,
            status__in=['ringing', 'active'],
        ).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)

        reject_call(call=call, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallSignalView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Relay WebRTC signaling")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = CallSignalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        call = Call.objects.filter(
            conversation_id=conversation_id,
            status__in=['ringing', 'active'],
        ).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            relay_signal(
                call=call,
                from_user=request.user,
                to_user_id=serializer.validated_data['to_user'],
                signal_type=serializer.validated_data['type'],
                payload=serializer.validated_data['payload'],
            )
        except CallError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Chat / Voice Calls'])
class CallMuteView(CallMembershipMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Update mute state")
    def post(self, request, conversation_id):
        if not self.check_membership(request, conversation_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = CallMuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        call = Call.objects.filter(
            conversation_id=conversation_id,
            status='active',
        ).first()
        if not call:
            return Response(status=status.HTTP_404_NOT_FOUND)

        update_mute(call=call, user=request.user, muted=serializer.validated_data['muted'])
        return Response(status=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 5: Add URL routes**

Append to `workspace/chat/urls.py` before the closing `]`:

```python
    # Voice calls
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/start',
        call_views.CallStartView.as_view(),
        name='chat-call-start',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/join',
        call_views.CallJoinView.as_view(),
        name='chat-call-join',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/leave',
        call_views.CallLeaveView.as_view(),
        name='chat-call-leave',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/reject',
        call_views.CallRejectView.as_view(),
        name='chat-call-reject',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/signal',
        call_views.CallSignalView.as_view(),
        name='chat-call-signal',
    ),
    path(
        'api/v1/chat/conversations/<uuid:conversation_id>/call/mute',
        call_views.CallMuteView.as_view(),
        name='chat-call-mute',
    ),
```

Also add import at the top of `workspace/chat/urls.py`:
```python
from . import call_views
```

- [ ] **Step 6: Run API tests**

Run: `python manage.py test workspace.chat.tests_call.CallAPITests -v2`
Expected: PASS

- [ ] **Step 7: Run all call tests**

Run: `python manage.py test workspace.chat.tests_call -v2`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add workspace/chat/call_serializers.py workspace/chat/call_views.py workspace/chat/urls.py workspace/chat/tests_call.py
git commit -m "feat(chat/api): add voice call REST endpoints with signaling relay"
```

---

## Task 7: CallManager JavaScript (WebRTC transport)

**Files:**
- Create: `workspace/chat/ui/static/chat/ui/js/call-manager.js`

- [ ] **Step 1: Create `call-manager.js`**

```javascript
// workspace/chat/ui/static/chat/ui/js/call-manager.js
//
// WebRTC P2P mesh transport abstraction.
// Communicates with the Alpine store via CustomEvents on window.
// This file has zero Alpine dependency — it can be swapped for a LiveKit SDK wrapper.

class CallManager {
  constructor({ csrfToken, currentUserId }) {
    this._csrf = csrfToken;
    this._currentUserId = currentUserId;
    this._peers = new Map();        // userId -> RTCPeerConnection
    this._localStream = null;
    this._callId = null;
    this._conversationId = null;
    this._monitorInterval = null;
    this._state = 'idle';
  }

  // ── Public API ────────────────────────────────────

  async startCall(conversationId) {
    try {
      this._localStream = await this._requestMicrophone();
    } catch (e) {
      this._emit('call:error', { type: 'mic_denied', message: e.message });
      return null;
    }

    this._conversationId = conversationId;
    this._setState('ringing_out');

    const resp = await this._post(`/api/v1/chat/conversations/${conversationId}/call/start`);
    if (!resp.ok) {
      this._cleanup();
      return null;
    }
    const data = await resp.json();
    this._callId = data.call_id;
    this._iceServers = data.ice_servers;
    return data;
  }

  async joinCall(conversationId) {
    try {
      this._localStream = await this._requestMicrophone();
    } catch (e) {
      this._emit('call:error', { type: 'mic_denied', message: e.message });
      return null;
    }

    this._conversationId = conversationId;
    this._setState('connecting');

    const resp = await this._post(`/api/v1/chat/conversations/${conversationId}/call/join`);
    if (!resp.ok) {
      this._cleanup();
      return null;
    }
    const data = await resp.json();
    this._callId = data.call_id;
    this._iceServers = data.ice_servers;

    // Create peer connections with existing participants (we are the newer joiner → we send offers)
    for (const participant of data.participants) {
      if (participant.id !== this._currentUserId) {
        await this._createPeerAndOffer(participant.id);
      }
    }

    this._startMonitoring();
    this._setState('active');
    this._emit('call:participants-changed', { participants: data.participants });
    return data;
  }

  async leaveCall() {
    if (!this._conversationId) return;
    // Use fetch with keepalive for reliability during page unload
    const url = `/api/v1/chat/conversations/${this._conversationId}/call/leave`;
    try {
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf },
        body: JSON.stringify({}),
        keepalive: true,
      });
    } catch (e) { /* ignore — cleanup proceeds regardless */ }
    this._cleanup();
    this._emit('call:ended', { reason: 'local' });
  }

  async rejectCall(conversationId) {
    await this._post(`/api/v1/chat/conversations/${conversationId}/call/reject`);
    this._setState('idle');
  }

  toggleMute() {
    if (!this._localStream) return false;
    const track = this._localStream.getAudioTracks()[0];
    if (!track) return false;
    track.enabled = !track.enabled;
    const muted = !track.enabled;
    if (this._conversationId) {
      this._post(`/api/v1/chat/conversations/${this._conversationId}/call/mute`, { muted });
    }
    return muted;
  }

  handleSignal(event) {
    const { from_user, type, payload } = event;
    if (type === 'offer') {
      this._handleOffer(from_user, payload);
    } else if (type === 'answer') {
      this._handleAnswer(from_user, payload);
    } else if (type === 'ice') {
      this._handleIceCandidate(from_user, payload);
    }
  }

  // Called when a new participant joins (SSE event)
  onParticipantJoined(userId) {
    // The new joiner sends offers to us; we just wait for their offer.
    // No action needed here — handleSignal will handle the incoming offer.
  }

  onParticipantLeft(userId) {
    const pc = this._peers.get(userId);
    if (pc) {
      pc.close();
      this._peers.delete(userId);
    }
  }

  // ── Internal ──────────────────────────────────────

  async _requestMicrophone() {
    return navigator.mediaDevices.getUserMedia({ audio: true });
  }

  async _createPeerAndOffer(userId) {
    const pc = this._createPeerConnection(userId);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await this._sendSignal(userId, 'offer', { sdp: offer.sdp, type: offer.type });
  }

  _createPeerConnection(userId) {
    const config = { iceServers: this._iceServers || [] };
    const pc = new RTCPeerConnection(config);
    this._peers.set(userId, pc);

    // Add local audio track
    if (this._localStream) {
      for (const track of this._localStream.getTracks()) {
        pc.addTrack(track, this._localStream);
      }
    }

    // Handle remote audio
    pc.ontrack = (event) => {
      const audio = new Audio();
      audio.srcObject = event.streams[0];
      audio.play().catch(() => {});
    };

    // ICE candidates
    pc.onicecandidate = (event) => {
      if (event.candidate) {
        this._sendSignal(userId, 'ice', event.candidate.toJSON());
      }
    };

    // Connection state changes
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === 'failed') {
        // Peer connection failed — remove locally and notify server
        this.onParticipantLeft(userId);
        // If all peers disconnected, leave the call
        if (this._peers.size === 0 && this._state === 'active') {
          this.leaveCall();
        }
      } else if (pc.iceConnectionState === 'disconnected') {
        this.onParticipantLeft(userId);
      }
      if (pc.iceConnectionState === 'connected' && this._state === 'connecting') {
        this._setState('active');
        this._startMonitoring();
      }
    };

    return pc;
  }

  async _handleOffer(fromUserId, payload) {
    const pc = this._createPeerConnection(fromUserId);
    await pc.setRemoteDescription(new RTCSessionDescription(payload));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    await this._sendSignal(fromUserId, 'answer', { sdp: answer.sdp, type: answer.type });

    if (this._state === 'connecting') {
      this._setState('active');
      this._startMonitoring();
    }
  }

  async _handleAnswer(fromUserId, payload) {
    const pc = this._peers.get(fromUserId);
    if (pc) {
      await pc.setRemoteDescription(new RTCSessionDescription(payload));
    }
  }

  async _handleIceCandidate(fromUserId, payload) {
    const pc = this._peers.get(fromUserId);
    if (pc) {
      await pc.addIceCandidate(new RTCIceCandidate(payload));
    }
  }

  async _sendSignal(toUserId, type, payload) {
    await this._post(
      `/api/v1/chat/conversations/${this._conversationId}/call/signal`,
      { to_user: toUserId, type, payload },
    );
  }

  _startMonitoring() {
    if (this._monitorInterval) return;
    this._monitorInterval = setInterval(() => this._monitorQuality(), 5000);
  }

  async _monitorQuality() {
    let totalLoss = 0;
    let totalRtt = 0;
    let count = 0;

    for (const [userId, pc] of this._peers) {
      try {
        const stats = await pc.getStats();
        stats.forEach((report) => {
          if (report.type === 'outbound-rtp' && report.kind === 'audio') {
            if (report.packetsLost !== undefined && report.packetsSent > 0) {
              totalLoss += report.packetsLost / report.packetsSent;
              count++;
            }
          }
          if (report.type === 'candidate-pair' && report.currentRoundTripTime) {
            totalRtt += report.currentRoundTripTime * 1000; // to ms
          }
        });
      } catch (e) { /* ignore */ }
    }

    if (count === 0) return;
    const avgLoss = totalLoss / count;
    const avgRtt = count > 0 ? totalRtt / count : 0;

    let targetBitrate = 48000;
    let level = null;
    if (avgLoss > 0.05 || avgRtt > 300) {
      targetBitrate = 16000;
      level = 'poor';
    } else if (avgLoss > 0.02 || avgRtt > 150) {
      targetBitrate = 24000;
      level = 'degraded';
    }

    if (level) {
      this._emit('call:quality-warning', { level, participantCount: this._peers.size + 1 });
    }

    // Apply bitrate limit
    for (const [, pc] of this._peers) {
      for (const sender of pc.getSenders()) {
        if (sender.track?.kind === 'audio') {
          try {
            const params = sender.getParameters();
            if (!params.encodings || params.encodings.length === 0) {
              params.encodings = [{}];
            }
            params.encodings[0].maxBitrate = targetBitrate;
            await sender.setParameters(params);
          } catch (e) { /* ignore */ }
        }
      }
    }
  }

  _cleanup() {
    for (const [, pc] of this._peers) {
      pc.close();
    }
    this._peers.clear();
    if (this._localStream) {
      this._localStream.getTracks().forEach((t) => t.stop());
      this._localStream = null;
    }
    if (this._monitorInterval) {
      clearInterval(this._monitorInterval);
      this._monitorInterval = null;
    }
    this._callId = null;
    this._conversationId = null;
    this._setState('idle');
  }

  _setState(state) {
    this._state = state;
    this._emit('call:state-changed', { state });
  }

  _emit(event, detail) {
    window.dispatchEvent(new CustomEvent(event, { detail }));
  }

  async _post(url, body = {}) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': this._csrf,
      },
      body: JSON.stringify(body),
    });
  }
}

// Export for use in Alpine store
window.CallManager = CallManager;
```

- [ ] **Step 2: Verify file syntax**

Open browser devtools → no JS errors on load.

- [ ] **Step 3: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/js/call-manager.js
git commit -m "feat(chat/ui): add CallManager WebRTC transport abstraction"
```

---

## Task 8: Alpine store + overlay UI + call button

**Files:**
- Modify: `workspace/chat/ui/templates/chat/ui/index.html:160-181` (header actions)
- Modify: `workspace/chat/ui/static/chat/ui/css/chat.css`

- [ ] **Step 1: Add `call-manager.js` script tag**

In `index.html`, add the script tag after the existing `chat.js` script:

```html
<script src="{% static 'chat/ui/js/call-manager.js' %}"></script>
```

- [ ] **Step 2: Add Alpine store `call` initialization**

In the `<script>` block where Alpine stores are defined (after the push store), add:

```javascript
Alpine.store('call', {
  state: 'idle',
  callId: null,
  conversationId: null,
  participants: [],
  muted: false,
  duration: 0,
  durationDisplay: '00:00',
  qualityWarning: null,
  expanded: false,
  incomingFrom: null,
  error: null,
  _manager: null,
  _timer: null,
  _ringtone: null,

  init() {
    this._manager = new CallManager({
      csrfToken: document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '',
      currentUserId: {{ request.user.id }},
    });

    window.addEventListener('call:state-changed', (e) => {
      this.state = e.detail.state;
      if (this.state === 'active') this._startTimer();
      if (this.state === 'idle') this._stopTimer();
    });
    window.addEventListener('call:participants-changed', (e) => {
      this.participants = e.detail.participants;
    });
    window.addEventListener('call:quality-warning', (e) => {
      this.qualityWarning = e.detail;
    });
    window.addEventListener('call:error', (e) => {
      this.error = e.detail;
      setTimeout(() => { this.error = null; }, 5000);
    });
    window.addEventListener('call:ended', () => {
      this._stopRingtone();
      this._stopTimer();
      this.state = 'idle';
      this.callId = null;
      this.conversationId = null;
      this.participants = [];
      this.muted = false;
      this.expanded = false;
      this.qualityWarning = null;
    });

    // beforeunload — leave call on tab close
    window.addEventListener('beforeunload', () => {
      if (this.state === 'active' || this.state === 'connecting') {
        this._manager.leaveCall();
      }
    });
  },

  async start(conversationId) {
    const data = await this._manager.startCall(conversationId);
    if (data) {
      this.callId = data.call_id;
      this.conversationId = conversationId;
    }
  },

  async accept() {
    this._stopRingtone();
    await this._manager.joinCall(this.conversationId);
  },

  async reject() {
    this._stopRingtone();
    await this._manager.rejectCall(this.conversationId);
    this.state = 'idle';
    this.incomingFrom = null;
  },

  async leave() {
    await this._manager.leaveCall();
  },

  toggleMute() {
    this.muted = this._manager.toggleMute();
  },

  toggle() {
    this.expanded = !this.expanded;
  },

  // Handle SSE call events
  handleSSE(event, data) {
    if (event === 'chat.call.incoming') {
      this.state = 'ringing_in';
      this.callId = data.call_id;
      this.conversationId = data.conversation_id;
      this.incomingFrom = { id: data.initiator_id, name: data.initiator_name };
      this._playRingtone();
    } else if (event === 'chat.call.signal') {
      this._manager.handleSignal(data);
    } else if (event === 'chat.call.participant_joined') {
      this._manager.onParticipantJoined(data.user_id);
      if (!this.participants.find(p => p.id === data.user_id)) {
        this.participants.push({ id: data.user_id, name: data.user_name });
      }
    } else if (event === 'chat.call.participant_left') {
      this._manager.onParticipantLeft(data.user_id);
      this.participants = this.participants.filter(p => p.id !== data.user_id);
    } else if (event === 'chat.call.ended' || event === 'chat.call.missed') {
      this._manager._cleanup();
      this._stopRingtone();
    } else if (event === 'chat.call.mute_changed') {
      const p = this.participants.find(pp => pp.id === data.user_id);
      if (p) p.muted = data.muted;
    }
  },

  _startTimer() {
    this.duration = 0;
    this._timer = setInterval(() => {
      this.duration++;
      const m = Math.floor(this.duration / 60);
      const s = this.duration % 60;
      this.durationDisplay = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }, 1000);
  },

  _stopTimer() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this.duration = 0;
    this.durationDisplay = '00:00';
  },

  _playRingtone() {
    try {
      this._ringtone = new Audio('/static/chat/ui/sounds/ringtone.mp3');
      this._ringtone.loop = true;
      this._ringtone.play().catch(() => {});
      setTimeout(() => this._stopRingtone(), 30000);
    } catch (e) { /* ignore */ }
  },

  _stopRingtone() {
    if (this._ringtone) {
      this._ringtone.pause();
      this._ringtone = null;
    }
  },
});
```

- [ ] **Step 3: Hook inline SSE events to call store**

In the existing SSE event handler (where `window.dispatchEvent(new CustomEvent('chat-message', ...))` is called), add handling for `chat.call.*` events:

```javascript
// Inside the SSE message handler
if (parsed.event.startsWith('chat.call.')) {
  Alpine.store('call').handleSSE(parsed.event, parsed.data);
  return;
}
```

- [ ] **Step 4: Add call button to conversation header**

In the header actions `<div>` at line 161, add before the search button:

```html
          <button
            class="btn btn-ghost btn-sm btn-circle"
            @click="$store.call.start(activeConversation.uuid)"
            :disabled="$store.call.state !== 'idle'"
            title="Start voice call"
          >
            <i data-lucide="phone" class="w-4 h-4"></i>
          </button>
```

- [ ] **Step 5: Add overlay component**

Add before the closing `</div>` of the main chat container:

```html
<!-- Voice call overlay -->
<template x-if="$store.call.state !== 'idle'">
  <div class="fixed bottom-4 right-4 z-50">
    <!-- Incoming call banner -->
    <template x-if="$store.call.state === 'ringing_in'">
      <div class="bg-base-100 border border-base-300 rounded-2xl shadow-2xl p-4 w-72 animate-bounce-slow">
        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-success/20 flex items-center justify-center">
            <i data-lucide="phone-incoming" class="w-5 h-5 text-success"></i>
          </div>
          <div>
            <p class="font-semibold text-sm" x-text="$store.call.incomingFrom?.name + ' vous appelle'"></p>
            <p class="text-xs text-base-content/60">Appel vocal</p>
          </div>
        </div>
        <div class="flex gap-2">
          <button class="btn btn-success btn-sm flex-1" @click="$store.call.accept()">
            <i data-lucide="phone" class="w-4 h-4"></i> Accepter
          </button>
          <button class="btn btn-error btn-sm flex-1" @click="$store.call.reject()">
            <i data-lucide="phone-off" class="w-4 h-4"></i> Refuser
          </button>
        </div>
      </div>
    </template>

    <!-- Ringing out -->
    <template x-if="$store.call.state === 'ringing_out'">
      <div class="bg-base-100 border border-base-300 rounded-full shadow-xl px-4 py-2 flex items-center gap-3">
        <span class="loading loading-dots loading-xs text-success"></span>
        <span class="text-sm">Appel en cours...</span>
        <button class="btn btn-error btn-xs btn-circle" @click="$store.call.leave()">
          <i data-lucide="phone-off" class="w-3 h-3"></i>
        </button>
      </div>
    </template>

    <!-- Active call -->
    <template x-if="$store.call.state === 'active' || $store.call.state === 'connecting'">
      <div
        class="bg-base-100 border border-base-300 rounded-2xl shadow-xl transition-all"
        :class="$store.call.expanded ? 'w-72 p-4' : 'px-4 py-2 rounded-full cursor-pointer'"
        @click="if (!$store.call.expanded) $store.call.toggle()"
      >
        <!-- Collapsed -->
        <template x-if="!$store.call.expanded">
          <div class="flex items-center gap-3">
            <span class="w-2 h-2 rounded-full bg-success animate-pulse"></span>
            <span class="text-sm font-medium" x-text="$store.call.durationDisplay"></span>
            <span class="text-xs text-base-content/60" x-text="$store.call.participants.length + ' pers.'"></span>
            <button
              class="btn btn-ghost btn-xs btn-circle"
              :class="$store.call.muted ? 'text-error' : ''"
              @click.stop="$store.call.toggleMute()"
            >
              <i :data-lucide="$store.call.muted ? 'mic-off' : 'mic'" class="w-3 h-3"></i>
            </button>
            <button class="btn btn-error btn-xs btn-circle" @click.stop="$store.call.leave()">
              <i data-lucide="phone-off" class="w-3 h-3"></i>
            </button>
          </div>
        </template>

        <!-- Expanded -->
        <template x-if="$store.call.expanded">
          <div>
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full bg-success animate-pulse"></span>
                <span class="text-sm font-semibold" x-text="$store.call.durationDisplay"></span>
              </div>
              <button class="btn btn-ghost btn-xs btn-circle" @click="$store.call.toggle()">
                <i data-lucide="minimize-2" class="w-3 h-3"></i>
              </button>
            </div>

            <!-- Quality warning -->
            <template x-if="$store.call.qualityWarning || $store.call.participants.length > 6">
              <div class="text-xs text-warning bg-warning/10 rounded px-2 py-1 mb-2">
                La qualit&eacute; peut &ecirc;tre d&eacute;grad&eacute;e
              </div>
            </template>

            <!-- Participants -->
            <div class="space-y-1 mb-3">
              <template x-for="p in $store.call.participants" :key="p.id">
                <div class="flex items-center gap-2 px-2 py-1 bg-base-200 rounded-lg">
                  <div class="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center text-xs" x-text="p.name?.[0]?.toUpperCase()"></div>
                  <span class="text-xs flex-1 truncate" x-text="p.name"></span>
                  <i x-show="p.muted" data-lucide="mic-off" class="w-3 h-3 text-error"></i>
                </div>
              </template>
            </div>

            <!-- Controls -->
            <div class="flex justify-center gap-3">
              <button
                class="btn btn-sm btn-circle"
                :class="$store.call.muted ? 'btn-error' : 'btn-ghost'"
                @click="$store.call.toggleMute()"
              >
                <i :data-lucide="$store.call.muted ? 'mic-off' : 'mic'" class="w-4 h-4"></i>
              </button>
              <button class="btn btn-error btn-sm btn-circle" @click="$store.call.leave()">
                <i data-lucide="phone-off" class="w-4 h-4"></i>
              </button>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<!-- Call error toast -->
<template x-if="$store.call.error">
  <div class="toast toast-end toast-bottom z-50">
    <div class="alert alert-error">
      <span x-text="$store.call.error.message || 'Erreur micro'"></span>
    </div>
  </div>
</template>
```

- [ ] **Step 6: Add overlay CSS**

Append to `workspace/chat/ui/static/chat/ui/css/chat.css`:

```css
/* Voice call overlay */
@keyframes bounce-slow {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
.animate-bounce-slow {
  animation: bounce-slow 2s ease-in-out infinite;
}
```

- [ ] **Step 7: Refresh Lucide icons after overlay render**

In the Alpine store `init()`, add a watcher:

```javascript
this.$watch && Alpine.effect(() => {
  const state = Alpine.store('call').state;
  if (state !== 'idle') {
    setTimeout(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); }, 50);
  }
});
```

Or add `x-init="$nextTick(() => lucide.createIcons())"` on the overlay container.

- [ ] **Step 8: Manual test**

1. Open chat in two browser tabs with different users
2. Click phone icon → verify "Appel en cours..." bubble appears
3. Second tab should show incoming call banner
4. Accept → verify active call overlay with timer
5. Toggle mute → verify icon changes
6. Hang up → verify overlay disappears and system message appears

- [ ] **Step 9: Commit**

```bash
git add workspace/chat/ui/templates/chat/ui/index.html workspace/chat/ui/static/chat/ui/css/chat.css workspace/chat/ui/static/chat/ui/js/call-manager.js
git commit -m "feat(chat/ui): add voice call overlay, Alpine store, and call button"
```

---

## Task 9: Ringtone audio file

**Files:**
- Create: `workspace/chat/ui/static/chat/ui/sounds/ringtone.mp3`

- [ ] **Step 1: Add a ringtone audio file**

Source a short (~3s), loopable, royalty-free ringtone MP3. Place it at:
`workspace/chat/ui/static/chat/ui/sounds/ringtone.mp3`

Keep it under 50KB for fast loading.

- [ ] **Step 2: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/sounds/ringtone.mp3
git commit -m "feat(chat/ui): add ringtone audio for incoming calls"
```

---

## Task 10: Integration test — full call flow

**Files:**
- Modify: `workspace/chat/tests_call.py`

- [ ] **Step 1: Write full-flow integration test**

```python
# workspace/chat/tests_call.py — add
class CallFlowIntegrationTest(APITestCase):
    """End-to-end test: start → join → signal → mute → leave → system message."""

    def setUp(self):
        self.alice = User.objects.create_user(username='alice_e2e', password='pass')
        self.bob = User.objects.create_user(username='bob_e2e', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='E2E', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_services.call_timeout')
    def test_full_call_flow(self, mock_timeout, mock_push, mock_sse):
        # Alice starts call
        self.client.force_authenticate(self.alice)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 201)
        call_id = resp.data['call_id']

        # Bob joins
        self.client.force_authenticate(self.bob)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        self.assertEqual(resp.status_code, 200)

        call = Call.objects.get(uuid=call_id)
        self.assertEqual(call.status, 'active')

        # Bob signals Alice
        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.uuid}/call/signal',
            {'to_user': self.alice.id, 'type': 'offer', 'payload': {'sdp': 'v=0...'}},
            format='json',
        )
        self.assertEqual(resp.status_code, 204)

        # Bob mutes
        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.uuid}/call/mute',
            {'muted': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 204)

        # Alice leaves
        self.client.force_authenticate(self.alice)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/leave')

        # Bob leaves — call should end
        self.client.force_authenticate(self.bob)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/leave')

        call.refresh_from_db()
        self.assertEqual(call.status, 'ended')

        # System message should exist
        from workspace.chat.models import Message
        system_msg = Message.objects.filter(
            conversation=self.conv,
            author__username='system',
        ).last()
        self.assertIsNotNone(system_msg)
        self.assertIn('Appel vocal', system_msg.body)
```

- [ ] **Step 2: Run integration test**

Run: `python manage.py test workspace.chat.tests_call.CallFlowIntegrationTest -v2`
Expected: PASS

- [ ] **Step 3: Run ALL tests**

Run: `python manage.py test -v2`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add workspace/chat/tests_call.py
git commit -m "test(chat): add full call flow integration test"
```
