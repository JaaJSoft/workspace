# Chat @Mentions Design

## Summary

Add @username mention support in chat conversations (groups and DMs). Mentions trigger higher-priority notifications and are displayed as colored badges in messages.

## Requirements

- Autocomplete dropdown when typing `@` in the message textarea
- Dropdown shows active conversation members + `@everyone` option
- Mentions stored as plain text `@username` in message body
- Backend renders `@username` as styled badge in `body_html`
- Mentioned users receive notifications with `priority='high'` instead of `normal`
- `@everyone` mentions all active members of the conversation

## Approach: Plain Text Mentions (Approach A)

Messages store `@username` as plain text in the `body` field. The rendering pipeline (`render_message_body`) converts them to styled HTML badges. The send API extracts mentioned usernames to dispatch high-priority notifications.

No new Django models or migrations required.

## Architecture

### 1. Autocompletion (Frontend - Alpine.js)

- Detect `@` keystrokes in the message textarea
- Show a dropdown above/below the cursor with conversation members
- Filter members by username/first_name/last_name as user types after `@`
- Include `@everyone` option for group conversations
- Keyboard navigation: arrows + Enter to select, Escape to close
- On selection: insert `@username ` (with trailing space) into textarea

**Data source:** `GET /api/v1/chat/conversations/<uuid>/members` (existing or new endpoint returning active members)

### 2. Message Body Rendering (Backend)

In `render_message_body()`, after mistune rendering, post-process `body_html`:

- Regex: `@(\w+)` — match potential mentions
- For each match, check if username exists in the system
- Replace with: `<span class="mention-badge" data-username="username">@username</span>`
- `@everyone` gets a distinct style variant

### 3. Notification Priority Escalation

In `MessageListView.post()`, after message creation:

1. Parse `body` to extract `@username` tokens and `@everyone`
2. Resolve usernames to User objects
3. For `@everyone`: expand to all active members (except author)
4. Call existing `notify_new_message()` with enhanced logic:
   - Mentioned users get `priority='high'`
   - Non-mentioned users get `priority='normal'` (existing behavior)

### 4. CSS Styling

```css
.mention-badge {
    background-color: rgba(var(--primary-rgb), 0.15);
    color: var(--primary);
    padding: 1px 4px;
    border-radius: 4px;
    font-weight: 500;
}
```

### 5. Unchanged

- No new Django models or migrations
- SSE system unchanged (notifications already propagate via existing SSE)
- Message editing works naturally (plain text `@username` in body)
- Message deletion unchanged

## Scope

- Groups and DMs
- Works with existing user search endpoint for autocomplete data
- Applies to new messages only (no retroactive mention detection)
