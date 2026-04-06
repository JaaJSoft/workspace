# Test Coverage Analysis

**Date:** 2026-04-06
**Total tests:** 1,309 across 54 test files

## Coverage by Module

| Module | Source LOC | Tests | Ratio | Rating |
|---|---|---|---|---|
| files | 6,738 | 529 | 0.078 | Good |
| calendar | 3,455 | 186 | 0.054 | Good |
| mail | 3,861 | 185 | 0.048 | Moderate |
| chat | 4,344 | 172 | 0.040 | Moderate |
| ai | 4,056 | 129 | 0.032 | Moderate |
| core | 1,292 | 37 | 0.029 | Low |
| users | 1,603 | 25 | 0.016 | Very Low |
| notifications | 512 | 20 | 0.039 | Low |
| common | 361 | 16 | 0.044 | Low |
| notes | 318 | 8 | 0.025 | Very Low |
| dashboard | 183 | 2 | 0.011 | Critical |

## Priority 1 — Critical Gaps

### Users module (25 tests / 1,603 LOC)

Only `test_banner_palettes.py` exists. Everything else is untested:

- **`views.py`** — 11 API endpoints: user search, password change, avatar upload/delete, user status, settings CRUD, user groups. Security-sensitive.
- **`presence_service.py`** — Online status tracking with cache/DB synchronization and activity throttling.
- **`settings_service.py`** — User settings CRUD with timezone resolution. Used across all modules.
- **`avatar_service.py`** — Image processing pipeline (crop, resize, WebP conversion).
- **`middleware.py`** — AJAX login redirect + presence tracking. Runs on every request.

### Dashboard module (2 tests / 183 LOC)

- **`views.py`** — Dashboard rendering with upcoming events, activity feed aggregation, module stats.

### Notes module (8 tests / 318 LOC)

- **`search.py`** — Note search with tag resolution and filtering.
- **`ui/views.py`** — Folder structure management, default folder creation/migration.

## Priority 2 — Significant Gaps in Tested Modules

### Chat — Services layer untested

172 tests cover API well, but these have zero tests:

- **`services.py`** — `user_conversation_ids()`, `get_active_membership()`, `get_or_create_dm()`, `render_message_body()`, `extract_mentions()`, `notify_new_message()` (notification merging logic).
- **`typing_service.py`** — Typing indicator state management.
- **`avatar_service.py`** — Group avatar processing.
- **`search.py`** — Message search.

### Mail — SMTP, views, and credentials untested

185 tests cover folders, labels, OAuth2. Missing:

- **`services/smtp.py`** — Email sending logic. Zero tests for the send path.
- **`services/credentials.py`** — Encryption/decryption of mail credentials.
- **`views.py`** — Main mail API endpoints (list, read, compose, reply, forward).
- **`tasks.py`** — IMAP sync background tasks.
- **`search.py`** — Mail search.

### AI — Client and image service untested

129 tests cover prompts, models, tools. Missing:

- **`client.py`** — AI client wrapper.
- **`image_service.py`** — AI image generation/editing.
- **`sse_provider.py`** — Real-time streaming of AI responses.

### Files — Service core and sync untested

529 tests (best coverage), but critical gaps:

- **`FileService.accessible_files_q()`** — Permission Q filter untested.
- **`FileService.get_permission()`** — Permission resolution untested.
- **`storage.py`** — Custom `OverwriteStorage` class untested.
- **`sync.py`** — Bidirectional file sync service untested.
- **`search.py`** — File search untested.

### Notifications — Service and views untested

20 tests focus only on push. Missing:

- **`services.py`** — `notify()`, `notify_many()`, `get_unread_count()`.
- **`views.py`** — Notification list/mark-read/delete API.
- **`sse_provider.py`** — Real-time notification delivery.

## Priority 3 — Smaller Gaps

### Core module

- **`views_sse.py`** — Global SSE endpoint coordinating all real-time features.
- **`module_registry.py`** — Central registry for search, pending actions, commands.
- **`tasks.py`** — Database maintenance (WAL checkpoint, VACUUM).

### Common module

- **`image_service.py`** — Shared image processing used by users and files.
- **`templatetags/ui_filters.py`** — `filesize` and `localtime_tag` filters.

### Calendar — Recurrence edge cases

- **`recurrence.py`** — Only tested indirectly. Needs dedicated tests for DST, timezone transitions, exception dates.
- **`upcoming.py`** — Upcoming event expansion untested.
- **`ai_tools.py`** — Calendar AI integration untested.

## Cross-Cutting Concerns Missing

1. **No SSE/real-time tests** — All SSE providers (chat, ai, notifications, files, users) are untested.
2. **No search tests** — Search functions in chat, mail, files, notes, and calendar are all untested.
3. **No middleware tests** — Both middleware classes in `users/middleware.py` untested.
4. **Sparse authorization negative tests** — Access control helpers referenced in CLAUDE.md are mostly untested. Few tests verify that unauthorized access is denied.
5. **No cross-module integration tests** — No tests for flows like "file shared -> notification -> SSE event".

## Recommended Implementation Order

| # | Target | Type | Why |
|---|---|---|---|
| 1 | `users/views.py` — All 11 endpoints | API | Security-sensitive, completely untested |
| 2 | `users/presence_service.py` | Unit | Core real-time feature |
| 3 | `users/settings_service.py` | Unit | Cross-module dependency |
| 4 | `chat/services.py` — Access control + DM | Unit | Authorization correctness |
| 5 | `mail/views.py` — Mail API | API | Core feature, zero coverage |
| 6 | `mail/services/smtp.py` | Unit (mocked) | Email sending reliability |
| 7 | `mail/services/credentials.py` | Unit | Security (encryption) |
| 8 | `notifications/views.py` + `services.py` | API + Unit | Notification correctness |
| 9 | `common/image_service.py` | Unit | Shared utility |
| 10 | `dashboard/views.py` | Integration | Landing page |
| 11 | `core/views_sse.py` | Unit | Real-time infrastructure |
| 12 | `calendar/recurrence.py` edge cases | Unit | Recurring event correctness |
| 13 | Search functions (all modules) | Unit | Currently zero coverage |
| 14 | `files/sync.py` | Unit | Data consistency |
| 15 | `ai/client.py` | Unit (mocked) | AI feature reliability |
