# Voice Chat — Design Spec

**Date:** 2026-03-18
**Status:** Draft

## Overview

Real-time voice calls in the existing chat module. Peer-to-peer mesh WebRTC for audio, signaled through the existing SSE + REST infrastructure. No SFU — the server handles signaling only, never touches audio. Self-hosted coturn for STUN/TURN.

## Decisions

| Topic | Decision |
|---|---|
| Scope | Real-time voice calls only (no voice messages) |
| Participants | 1-to-1 + group |
| Transport | P2P mesh (full-mesh `RTCPeerConnection`) |
| Participant limit | No hard limit. UI warning at 7+. Adaptive bitrate degradation. |
| Future SFU | Transport layer abstracted behind `CallManager` interface for future swap |
| Signaling | SSE (server→client) + REST POST (client→server). No WebSockets. |
| Call UI | Floating overlay bubble, persistent across conversation switches |
| Incoming calls | Push notification + in-app banner with Accept/Reject |
| STUN/TURN | Public STUN (Google) + self-hosted coturn with ephemeral credentials |

## Architecture

```
Browser A                                              Browser B
┌──────────────────────┐                               ┌──────────────────────┐
│ Alpine store('call') │                               │ Alpine store('call') │
│ CallManager          │                               │ CallManager          │
│ RTCPeerConnection ×N │── audio P2P (or via TURN) ──►│ RTCPeerConnection ×N │
└──────────┬───────────┘                               └──────────┬───────────┘
           │ REST + SSE                                           │ REST + SSE
           ▼                                                      ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                              Django                                        │
│  REST endpoints: call/start, call/join, call/leave, call/signal, etc.     │
│  SSE events: call.incoming, call.signal, call.participant_joined, etc.    │
│  Celery: timeout missed calls, cleanup stale participants                 │
├───────────────┬───────────────────────────────────────┬────────────────────┤
│    Redis      │              Celery                   │     coturn         │
│  (SSE pub/sub)│  (push notif, timeouts, cleanup)     │  (STUN + TURN)    │
└───────────────┴───────────────────────────────────────┴────────────────────┘
```

The server never sees or relays audio. It only:
1. Manages call state (who is in which call)
2. Relays signaling messages (SDP offers/answers, ICE candidates) via SSE
3. Sends push notifications for incoming calls
4. Cleans up stale state (timeouts, disconnects)

**Prerequisite:** Redis is required for voice calls. The SSE cache-polling fallback (2s intervals) is too slow for WebRTC signaling. If Redis is unavailable, the call button should be hidden.

## SSE Signaling — Inline Events

WebRTC signaling (SDP offers/answers, ICE candidates) requires sub-second delivery. The current SSE pub/sub path triggers a database poll on notification, which adds latency and is unsuitable for ephemeral signaling data.

**Solution: inline events.** Instead of storing signaling data in the DB and triggering a poll, the signaling payload is embedded directly in the Redis pub/sub message. The SSE stream handler detects inline events and yields them immediately, bypassing the provider poll cycle entirely.

### How it works

1. `POST call/signal` receives the SDP/ICE payload
2. Instead of calling `notify_sse('chat', user_id)`, it publishes an **inline event** to Redis:
   ```python
   redis.publish(f'sse:user:{user_id}', orjson.dumps({
       'inline': True,
       'event': 'chat.signal',
       'data': {'call_id': ..., 'from_user': ..., 'type': ..., 'payload': ...},
   }))
   ```
3. In `views_sse.py`, the pub/sub message handler checks for `inline: True`:
   - If present: yields the SSE event immediately from the embedded data (no poll)
   - If absent: existing behavior (triggers provider poll)

This requires a small modification to `_event_stream_pubsub()` in `workspace/core/views_sse.py` (~10 lines). The change is backwards-compatible — existing non-inline notifications continue to work as before.

**Latency:** Redis pub/sub delivery is ~1-5ms. Combined with the 5s blocking `get_message(timeout=5)`, worst case is near-instant (message arrives while blocking) to ~5s (just missed the window). To improve this for signaling, we reduce the timeout to `0.5s` when a call is active (detected by tracking whether any `call.*` inline events have been received recently).

### Other call events (non-signal)

Events like `call.incoming`, `call.participant_joined`, `call.ended`, etc. are **also delivered as inline events** since they don't correspond to any database-stored data that a provider could poll for. The `ChatSSEProvider` does not need modification for call events — they all go through the inline path.

## Data Model

Two new models in `workspace/chat/models.py`:

### Call

| Field | Type | Description |
|---|---|---|
| `uuid` | UUIDField (PK) | Primary key |
| `conversation` | FK → Conversation | Which conversation this call belongs to |
| `initiator` | FK → User | Who started the call |
| `status` | CharField | `ringing` → `active` → `ended`, or `ringing` → `missed` |
| `started_at` | DateTimeField (null) | Set when 2nd participant joins |
| `ended_at` | DateTimeField (null) | Set when last participant leaves |
| `created_at` | DateTimeField (auto) | When the call was initiated |

**Concurrency guard:** `UniqueConstraint(fields=['conversation'], condition=Q(status__in=['ringing', 'active']), name='one_active_call_per_conversation')`. This prevents concurrent `call/start` race conditions at the database level — at most one non-ended call per conversation.

### CallParticipant

| Field | Type | Description |
|---|---|---|
| `uuid` | UUIDField (PK) | Primary key |
| `call` | FK → Call | Which call |
| `user` | FK → User | Which user |
| `joined_at` | DateTimeField (auto) | When they joined |
| `left_at` | DateTimeField (null) | When they left (null = still in call) |
| `muted` | BooleanField | Muted state (synced from client) |

**Constraint:** Conditional unique constraint `UniqueConstraint(fields=['call', 'user'], condition=Q(left_at__isnull=True), name='unique_active_call_participant')`. This allows only one active participation per user per call, while permitting historical rejoin records (user leaves, then rejoins — creates a new row).

### System Messages

When a call ends or is missed, a `Message` is created in the conversation using a **system bot user** (avoids making `Message.author` nullable). The system user is a `User` instance with `is_active=False` and a reserved username (e.g., `system`), created via a data migration.

- Call ended: "📞 Appel vocal · {n} participants · {duration}"
- Call missed: "📞 Appel manqué de {initiator}"
- Call rejected (DM): "📞 Appel refusé"

## API — Signaling Endpoints

All require `IsAuthenticated` + active membership in the conversation.

### POST `/api/v1/chat/conversations/{conversation_id}/call/start`

Requests microphone permission on the client first. Creates a `Call(status='ringing')`, adds initiator as `CallParticipant`. Sends inline SSE `call.incoming` to all other conversation members. Triggers push notification. Schedules Celery timeout task (30s).

**Response:** `201 {call_id, ice_servers}`

### POST `/api/v1/chat/conversations/{conversation_id}/call/join`

Requests microphone permission on the client first. Adds user as `CallParticipant`. If this is the 2nd participant, sets `Call.status='active'` and `started_at`. Sends inline SSE `call.participant_joined` to all active participants.

**Constraints:**
- Rejects if user already in another active call
- Rejects if call status is `ended` or `missed`

**Response:** `200 {call_id, participants[], ice_servers}`

### POST `/api/v1/chat/conversations/{conversation_id}/call/leave`

Sets `left_at` on the participant. If no active participants remain, sets `Call.status='ended'`, `ended_at`, creates system message. Sends inline SSE `call.participant_left`.

**Response:** `204`

### POST `/api/v1/chat/conversations/{conversation_id}/call/reject`

User declines the call. Sends inline SSE `call.rejected` to initiator.

**Group semantics:** Individual rejects are silently recorded (no state change). The call transitions to `missed` only if the 30s timeout fires with zero joins — regardless of how many members rejected.

**Response:** `204`

### POST `/api/v1/chat/conversations/{conversation_id}/call/signal`

Relays WebRTC signaling to a specific participant via inline SSE.

**Body:** `{to_user: uuid, type: 'offer'|'answer'|'ice', payload: {...}}`

**Response:** `204`

The server does not interpret the signaling payload — it forwards it as-is via inline SSE `call.signal` to the target user only. **Validation:** the server must verify that both the sender and `to_user` have active `CallParticipant` records (`left_at__isnull=True`) for the current call before forwarding. Returns `403` if either party is not an active participant.

### POST `/api/v1/chat/conversations/{conversation_id}/call/mute`

**Body:** `{muted: true|false}`

Updates `CallParticipant.muted`. Sends inline SSE `call.mute_changed` to all participants.

**Response:** `204`

## SSE Events

All delivered as **inline events** via Redis pub/sub (not via provider polling):

| Event | Recipients | Payload |
|---|---|---|
| `call.incoming` | All conversation members (except initiator) | `{call_id, conversation_id, initiator_id, initiator_name}` |
| `call.participant_joined` | All active participants | `{call_id, user_id, user_name}` |
| `call.participant_left` | All active participants | `{call_id, user_id}` |
| `call.ended` | All conversation members | `{call_id, duration, participant_count}` |
| `call.missed` | Initiator + all conversation members | `{call_id, conversation_id}` |
| `call.signal` | Single target user (private) | `{call_id, from_user, type, payload}` |
| `call.mute_changed` | All active participants | `{call_id, user_id, muted}` |
| `call.rejected` | Initiator only | `{call_id, user_id}` |

## Call Establishment Flow (1-to-1)

```
Alice                         Server                         Bob
  │                              │                              │
  │── POST call/start ──────────►│                              │
  │◄── 201 {call_id, ice} ──────│                              │
  │                              │── SSE call.incoming ────────►│
  │                              │── Push notification ────────►│
  │                              │                              │
  │                              │◄──── POST call/join ─────────│
  │◄── SSE participant_joined ──│──── 200 {participants, ice} ►│
  │                              │                              │
  │── POST call/signal ─────────►│  (SDP offer, to: bob)       │
  │                              │── SSE call.signal ──────────►│
  │                              │                              │
  │                              │◄──── POST call/signal ───────│  (SDP answer, to: alice)
  │◄── SSE call.signal ─────────│                              │
  │                              │                              │
  │── POST call/signal ─────────►│  (ICE candidates)           │
  │◄── SSE call.signal ─────────│◄──── POST call/signal ───────│
  │                              │                              │
  │◄═══════════════ P2P audio (direct or via TURN) ═══════════►│
```

## Call Establishment Flow (Group, N participants)

When a new participant joins an active group call:

1. New user calls `POST call/join` → receives list of current participants + ICE servers
2. Server sends SSE `call.participant_joined` to all existing participants
3. **The new participant initiates a peer connection with each existing participant:**
   - For each existing participant: creates `RTCPeerConnection`, sends SDP offer via `POST call/signal`
   - Each existing participant receives the offer via SSE, creates answer, sends back via `POST call/signal`
4. ICE candidates exchanged for each pair
5. Audio flows P2P between all pairs (full mesh)

Convention: the **newer joiner always sends the offer** to avoid collision.

## Frontend Architecture

### File: `call-manager.js` — Pure JS, no Alpine dependency

The transport abstraction layer. This is the file that would be replaced by a LiveKit SDK wrapper if migrating to SFU.

**Public interface:**

```
CallManager
├── startCall(conversationId)       → requests mic, then initiates call via REST
├── joinCall(callId)                → requests mic, then joins call, establishes peer connections
├── leaveCall()                     → closes all connections, notifies server
├── rejectCall(callId)              → declines incoming call
├── toggleMute()                    → mutes/unmutes local audio
├── handleSignal(event)             → processes incoming SDP/ICE from SSE
│
│  Internal:
├── _createPeerConnection(userId)   → creates RTCPeerConnection with audio track
├── _negotiate(userId)              → SDP offer/answer exchange
├── _monitorQuality()               → getStats() every 5s, adapts bitrate
├── _sendSignal(toUser, type, payload) → POST call/signal
├── _requestMicrophone()            → getUserMedia({audio: true}), handles denial
│
│  Events emitted (CustomEvent on window):
├── 'call:state-changed'            → {state: 'idle'|'ringing_out'|'ringing_in'|'connecting'|'active'}
├── 'call:participants-changed'     → {participants: [{id, name, muted, speaking}]}
├── 'call:quality-warning'          → {level: 'degraded'|'poor', participantCount}
├── 'call:error'                    → {type: 'mic_denied'|'mic_unavailable'|'connection_failed', message}
└── 'call:ended'                    → {duration, reason}
```

**Microphone permission:** `_requestMicrophone()` is called before `startCall` and `joinCall`. If the user denies permission, a `call:error` event is emitted with `type: 'mic_denied'` and the call is not started/joined. The Alpine store displays an error using the existing `inline_alert` partial with `type="error"`.

**State `connecting`:** This is a purely client-side state that occurs between the `call/join` REST response and the first `RTCPeerConnection` reaching the `connected` ICE state. No server-side event drives this transition.

### File: Alpine store in chat template — consumes CallManager events

```
Alpine.store('call', {
    state: 'idle',              // idle|ringing_out|ringing_in|connecting|active
    callId: null,
    conversationId: null,
    participants: [],
    muted: false,
    duration: 0,                // local timer (setInterval)
    qualityWarning: null,
    expanded: false,            // overlay collapsed/expanded
    incomingFrom: null,         // {id, name} for ringing_in state
    error: null,                // {type, message} for mic/connection errors

    start(conversationId),
    accept(),
    reject(),
    leave(),
    toggleMute(),
    toggle(),                   // expand/collapse overlay
})
```

### Overlay UI States

| State | Rendering |
|---|---|
| `idle` | Nothing shown |
| `ringing_in` | Full-width banner at top: "{name} vous appelle" + Accept (green) / Reject (red) buttons. Ringtone audio plays. |
| `ringing_out` | Floating bubble: "Appel en cours..." with pulse animation + Cancel button |
| `active` (collapsed) | Compact bubble: `🟢 02:34 · 3 pers. [🎤] [📕]` |
| `active` (expanded) | Expanded bubble: participant list with speaking indicators (audio level), mute/hangup controls |
| `error` | Inline alert (via `inline_alert` partial) with error message |

### Call Button

Phone icon added to the conversation header (next to existing action buttons). Disabled if a call is already active in that conversation. Hidden if Redis is unavailable (no `call` feature flag).

## Adaptive Quality

`CallManager._monitorQuality()` runs every 5 seconds during an active call:

1. Calls `RTCPeerConnection.getStats()` on each peer connection
2. Reads `outbound-rtp` stats: packets sent, packets lost, round-trip-time
3. Decision matrix:

| Condition | Action |
|---|---|
| Packet loss < 2%, RTT < 150ms | Normal: bitrate 48kbps (Opus default) |
| Packet loss 2-5% or RTT 150-300ms | Degraded: reduce to 24kbps, emit `quality-warning` |
| Packet loss > 5% or RTT > 300ms | Poor: reduce to 16kbps, emit `quality-warning` |
| Conditions improve for 15s | Gradually restore bitrate |

4. Bitrate adjusted via `RTCRtpSender.setParameters()` on each sender
5. At 7+ participants, UI shows persistent warning: "La qualité peut être dégradée avec beaucoup de participants"

## coturn Configuration

### Docker-compose service

```yaml
coturn:
  image: coturn/coturn:latest
  network_mode: host
  volumes:
    - ./coturn/turnserver.conf:/etc/turnserver.conf:ro
  environment:
    - TURN_SECRET=${TURN_SECRET}
  restart: unless-stopped
```

### Ephemeral credentials (TURN REST API)

Instead of static credentials, the backend generates short-lived credentials for each call participant:

- **Username:** `{expiry_timestamp}:{user_uuid}`
- **Credential:** `HMAC-SHA1(TURN_SECRET, username)`
- **TTL:** 24 hours
- Returned in the `ice_servers` field of `call/start` and `call/join` responses

coturn validates these credentials autonomously — no database lookup needed.

### Environment variables

| Variable | Description | Example |
|---|---|---|
| `TURN_SECRET` | Shared secret between Django and coturn | Random 32+ char string |
| `TURN_SERVER_URL` | TURN server URL | `turn:myserver.com:3478` |
| `STUN_SERVER_URL` | STUN server URL (optional) | `stun:stun.l.google.com:19302` (default) |

## Edge Cases

### Timeouts & Cleanup

- **No answer (30s):** Celery task sets `Call.status='missed'`, creates system message "📞 Appel manqué de {initiator}". Sends inline SSE `call.missed` to initiator (transitions UI from `ringing_out` to `idle`).
- **Participant disconnect (tab closed/crash):** `navigator.sendBeacon()` in `beforeunload` handler sends `POST call/leave` (more reliable than fetch during unload). Safety net: Celery task every 15s checks for participants whose SSE connection has been inactive for 30s and removes them. Client-side: `RTCPeerConnection.oniceconnectionstatechange` — when ICE state goes to `disconnected`/`failed` for a peer, notify other participants.
- **Last participant leaves:** `Call.status='ended'`, `ended_at` set, system message created with duration.

### Concurrency guards

- **One call per conversation:** `POST call/start` relies on the `one_active_call_per_conversation` unique constraint (see Data Model) to prevent race conditions at the DB level. Also uses `select_for_update()` on the conversation as an application-level guard.
- **One call per user:** `POST call/join` rejects if the user already has a `CallParticipant(left_at__isnull=True)` on any call.

### Permissions

- Only active conversation members (`ConversationMember(left_at__isnull=True)`) can start or join calls.
- Uses existing `user_conversation_ids(user)` helper.

### Ringtone

- Client-side audio: `new Audio('/static/chat/sounds/ringtone.mp3')`
- Auto-stops after 30s (synced with server timeout)
- Push notification uses `tag: 'call:{call_id}'` to replace instead of stack

### Group call rejection semantics

Individual rejects are silently recorded — no call state change. The call transitions to `missed` only if the 30s timeout fires with zero joins, regardless of how many members rejected. This avoids the complexity of tracking "all members rejected" in groups of varying size.

## Call History

Past calls are visible in the chat as system messages (with duration, participant count). A dedicated call log view is deferred to a later version.

## Files to Create/Modify

### New files
- `workspace/chat/call_views.py` — Call API views
- `workspace/chat/call_serializers.py` — DRF serializers for call endpoints
- `workspace/chat/call_tasks.py` — Celery tasks (timeout, stale participant cleanup)
- `workspace/chat/call_services.py` — Business logic (create call, join, leave, generate TURN credentials, inline SSE publishing)
- `workspace/chat/ui/static/chat/ui/js/call-manager.js` — WebRTC transport abstraction
- `workspace/chat/ui/static/chat/ui/sounds/ringtone.mp3` — Ringtone audio file
- `workspace/chat/migrations/XXXX_add_call_models.py` — Auto-generated migration
- `workspace/chat/migrations/XXXX_create_system_user.py` — Data migration for system bot user
- `docs/deployments/docker-compose/coturn/turnserver.conf` — coturn config

### Modified files
- `workspace/chat/models.py` — Add `Call`, `CallParticipant` models
- `workspace/chat/urls.py` — Add call endpoints (no trailing slashes)
- `workspace/core/views_sse.py` — Add inline event support in `_event_stream_pubsub()`
- `workspace/core/sse_registry.py` — Add `notify_sse_inline()` helper for publishing inline events
- `workspace/chat/ui/templates/chat/ui/index.html` — Add call button, overlay component, Alpine store
- `workspace/chat/ui/static/chat/ui/css/chat.css` — Overlay styles
- `workspace/settings.py` — Add TURN_SECRET, TURN_SERVER_URL, STUN_SERVER_URL settings
- `docs/deployments/docker-compose/docker-compose.yml` — Add coturn service, add Redis service
