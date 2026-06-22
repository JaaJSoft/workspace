# Chat

Direct and group messaging with real-time updates, Markdown support, and AI bot integration.

![Chat](../images/chat_1.png)

## Features

- **Direct messages** - One-on-one conversations between users
- **Group conversations** - Create named groups with multiple members
- **Real-time updates** - Live message delivery via Server-Sent Events (SSE)
- **Rich Markdown** - Format messages with Markdown, including syntax-highlighted code blocks
- **Emoji reactions** - React to messages with emojis
- **File attachments** - Attach files directly to messages
- **Message search** - Search across conversation history
- **Pinning** - Pin important messages for easy reference
- **Editing** - Edit sent messages after the fact
- **Read receipts** - See who has read your messages
- **AI bot integration** - Conversational AI assistants with system prompts, vision, function calling, and memory
- **Presence indicators** - See who is online in real time
- **Audio calls** - Join-in-progress WebRTC voice calls in direct messages and groups (not available in AI bot conversations)

## Configuration

Audio calls work out of the box with a public STUN server. The following
environment variables are optional (defaults shown):

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_CALL_ICE_SERVERS` | `stun:stun.l.google.com:19302` | Comma-separated STUN/TURN URLs. Add a TURN server for calls behind strict NATs/firewalls. |
| `CHAT_CALL_TURN_USERNAME` | (empty) | Username attached to every `turn:`/`turns:` URL. |
| `CHAT_CALL_TURN_CREDENTIAL` | (empty) | Credential attached to every `turn:`/`turns:` URL. |
| `CHAT_CALL_MAX_PARTICIPANTS` | `6` | Maximum simultaneous participants per call. |
| `CHAT_CALL_PRESENCE_TTL` | `12` | Seconds before a silent participant is dropped from a call. |

A call ends automatically once its last participant leaves or stops sending
heartbeats. The `chat.end_stale_calls` Celery beat task reaps calls whose
participants all vanished without a clean leave, so running Celery beat in
production is recommended.

### Call connection diagnostic

The "Test call connection" button in the chat preferences runs a non-intrusive
self-test: microphone access, ICE/STUN-TURN reachability, and a WebRTC loopback whose
signaling round-trips through `POST /api/v1/chat/call/diagnostic/signal`. It does
not start a real call and does not notify other members.

**Run it in a single tab.** The diagnostic echo reuses the per-user call mailbox,
which is drained on read. With the app open in a second tab, that tab can consume
an echo meant for the diagnostic and make the loopback step time out. This mirrors
the behavior of real call signaling, which is also per-user.

## API

All endpoints under `/api/v1/chat/` - see the [Swagger UI](/schema/swagger-ui/) for full documentation.
