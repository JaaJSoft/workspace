# Chat @Mentions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add @username mention autocomplete in chat with badge rendering and high-priority notifications for mentioned users.

**Architecture:** Plain text `@username` tokens in message body. Backend post-processes `render_message_body()` to convert tokens to styled HTML badges. The send API extracts mentions and passes a `mentioned_user_ids` set to `notify_new_message()` to escalate notification priority. Frontend adds an autocomplete dropdown triggered by `@` in the textarea, using conversation member data already available in Alpine state.

**Tech Stack:** Django, DRF, mistune (markdown), Alpine.js, DaisyUI/Tailwind CSS

---

### Task 1: Backend — Mention-aware message rendering

**Files:**
- Modify: `workspace/chat/services.py:68-77` (render_message_body)

**Step 1: Add mention rendering to `render_message_body()`**

In `workspace/chat/services.py`, modify `render_message_body()` to post-process the HTML output, replacing `@username` tokens with styled badge spans. The function needs to accept an optional set of valid usernames to avoid false positives.

```python
import re

def render_message_body(body, valid_usernames=None):
    """Render markdown body to HTML suitable for chat messages.

    If valid_usernames is provided, @username tokens matching those usernames
    are rendered as mention badges. @everyone is always rendered.
    """
    html = _markdown(body)
    if valid_usernames is None:
        return html

    def _replace_mention(match):
        username = match.group(1)
        if username == 'everyone':
            return '<span class="mention-badge mention-everyone">@everyone</span>'
        if username in valid_usernames:
            return f'<span class="mention-badge" data-username="{username}">@{username}</span>'
        return match.group(0)

    html = re.sub(r'@(\w+)', _replace_mention, html)
    return html
```

**Step 2: Add a helper to extract mentioned usernames from raw body**

```python
def extract_mentions(body):
    """Extract @username tokens from message body text.

    Returns a set of lowercase usernames (excluding 'everyone').
    Also returns whether @everyone was used.
    """
    tokens = set(re.findall(r'@(\w+)', body))
    has_everyone = 'everyone' in tokens
    tokens.discard('everyone')
    return tokens, has_everyone
```

**Step 3: Commit**

```bash
git add workspace/chat/services.py
git commit -m "feat(chat): add mention-aware message body rendering"
```

---

### Task 2: Backend — Wire mentions into message send API

**Files:**
- Modify: `workspace/chat/views.py:361-470` (MessageListView.post)
- Modify: `workspace/chat/services.py:95-166` (notify_new_message)

**Step 1: Update `MessageListView.post()` to extract mentions and pass valid usernames to render**

In `workspace/chat/views.py`, after the body is validated and before creating the message, resolve mentions:

```python
# After line 374 (body = serializer.validated_data.get('body', '').strip())
# and before line 397 (body_html = render_message_body(body) if body else '')

from .services import extract_mentions

# Extract mentions and resolve to real usernames
mentioned_usernames = set()
mentioned_user_ids = set()
has_everyone = False
if body:
    raw_mentions, has_everyone = extract_mentions(body)
    if raw_mentions or has_everyone:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        mentioned_users = User.objects.filter(
            username__in=raw_mentions
        ).values_list('id', 'username')
        mentioned_user_ids = {uid for uid, _ in mentioned_users}
        mentioned_usernames = {uname for _, uname in mentioned_users}
        mentioned_usernames.add('everyone')  # always valid for rendering

body_html = render_message_body(body, valid_usernames=mentioned_usernames) if body else ''
```

Then update the `notify_new_message` call (around line 450):

```python
notify_new_message(conversation, request.user, body, mentioned_user_ids=mentioned_user_ids, mention_everyone=has_everyone)
```

**Step 2: Update `notify_new_message()` to accept and use mention info**

In `workspace/chat/services.py`, modify `notify_new_message`:

```python
def notify_new_message(conversation, author, body, mentioned_user_ids=None, mention_everyone=False):
```

In the `for uid in member_ids:` loop, determine priority:

```python
    if mentioned_user_ids is None:
        mentioned_user_ids = set()

    for uid in member_ids:
        is_mentioned = uid in mentioned_user_ids or mention_everyone
        priority = 'high' if is_mentioned else 'normal'

        # Try to merge into an existing unread notification...
        existing = Notification.objects.filter(
            recipient_id=uid,
            origin='chat',
            url=conv_url,
            read_at__isnull=True,
        ).first()

        if existing:
            existing.body = preview
            existing.title = title_single
            existing.actor = author
            if is_mentioned and existing.priority != 'urgent':
                existing.priority = priority
            existing.save(update_fields=['body', 'title', 'actor', 'priority'])
            Notification.objects.filter(pk=existing.pk).update(created_at=timezone.now())
            _notify_sse('notifications', uid)
        else:
            notif = Notification.objects.create(
                recipient_id=uid,
                origin='chat',
                icon=icon,
                color=color,
                title=title_single,
                body=preview,
                url=conv_url,
                actor=author,
                priority=priority,
            )
            _notify_sse('notifications', uid)
            send_push_notification.delay(str(notif.uuid))
```

**Step 3: Update the edit message handler to also render mentions**

In `MessageDetailView.patch()`, find the line that calls `render_message_body(body)` and update it to also resolve valid usernames, similar to the send flow.

**Step 4: Commit**

```bash
git add workspace/chat/services.py workspace/chat/views.py
git commit -m "feat(chat): wire mentions into send API with high-priority notifications"
```

---

### Task 3: Frontend — Mention autocomplete in textarea

**Files:**
- Modify: `workspace/chat/ui/static/chat/ui/js/chat.js` (Alpine state + new methods)
- Modify: `workspace/chat/ui/templates/chat/ui/index.html` (dropdown HTML near textarea)

**Step 1: Add mention state to Alpine `chatApp()`**

Add these properties after the existing state declarations (around line 55):

```javascript
    // Mention autocomplete
    mentionActive: false,
    mentionQuery: '',
    mentionResults: [],
    mentionHighlight: -1,
    mentionStartPos: -1,
```

**Step 2: Add mention detection in `handleInputKeydown()`**

In `handleInputKeydown()` (line 1910), add mention-specific key handling before the existing handlers. When the mention dropdown is active, Arrow keys, Enter, and Escape should control it instead of the normal behavior:

```javascript
    // ── Mention autocomplete navigation ──
    if (this.mentionActive) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.mentionHighlight = (this.mentionHighlight + 1) % this.mentionResults.length;
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.mentionHighlight = this.mentionHighlight <= 0
          ? this.mentionResults.length - 1
          : this.mentionHighlight - 1;
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        if (this.mentionHighlight >= 0 && this.mentionHighlight < this.mentionResults.length) {
          this.insertMention(this.mentionResults[this.mentionHighlight]);
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        this.closeMentionDropdown();
        return;
      }
    }
```

**Step 3: Add `@input` handler for mention detection on the textarea**

Add a new method `handleMentionInput()` and wire it to the textarea's `@input` event alongside the existing `autoResize`:

```javascript
    handleMentionInput() {
      const ta = this.$refs.messageInput;
      if (!ta) return;
      const pos = ta.selectionStart;
      const text = ta.value.substring(0, pos);

      // Find the last '@' that starts a mention (preceded by start-of-string or whitespace)
      const match = text.match(/(?:^|\s)@(\w*)$/);
      if (match) {
        this.mentionActive = true;
        this.mentionQuery = match[1].toLowerCase();
        this.mentionStartPos = pos - match[1].length - 1; // position of '@'
        this.filterMentionResults();
      } else {
        this.closeMentionDropdown();
      }
    },

    filterMentionResults() {
      if (!this.activeConversation?.members) {
        this.mentionResults = [];
        return;
      }
      const q = this.mentionQuery;
      let results = [];

      // Add @everyone option for group conversations
      if (this.activeConversation.kind === 'group') {
        if (!q || 'everyone'.startsWith(q)) {
          results.push({ username: 'everyone', first_name: 'Notify', last_name: 'everyone', id: null });
        }
      }

      // Filter conversation members (exclude self)
      for (const m of this.activeConversation.members) {
        if (m.user.id === this.currentUserId) continue;
        const u = m.user;
        const searchStr = `${u.username} ${u.first_name || ''} ${u.last_name || ''}`.toLowerCase();
        if (!q || searchStr.includes(q)) {
          results.push({ username: u.username, first_name: u.first_name, last_name: u.last_name, id: u.id });
        }
      }

      this.mentionResults = results.slice(0, 8);
      this.mentionHighlight = results.length > 0 ? 0 : -1;
    },

    insertMention(user) {
      const ta = this.$refs.messageInput;
      if (!ta) return;
      const before = ta.value.substring(0, this.mentionStartPos);
      const after = ta.value.substring(ta.selectionStart);
      const mention = `@${user.username} `;
      this.messageBody = before + mention + after;
      this.closeMentionDropdown();
      this.$nextTick(() => {
        const newPos = before.length + mention.length;
        ta.setSelectionRange(newPos, newPos);
        ta.focus();
      });
    },

    closeMentionDropdown() {
      this.mentionActive = false;
      this.mentionQuery = '';
      this.mentionResults = [];
      this.mentionHighlight = -1;
      this.mentionStartPos = -1;
    },
```

**Step 4: Update the textarea in `index.html`**

Change the textarea (around line 381-390) to also call `handleMentionInput()`:

```html
<textarea
  x-ref="messageInput"
  x-model="messageBody"
  @keydown="handleInputKeydown($event)"
  @input="autoResize($el); handleMentionInput()"
  @paste="handlePaste($event)"
  class="textarea textarea-ghost w-full min-h-[2.5rem] max-h-[8rem] resize-none leading-snug px-3 py-1.5 focus:outline-none"
  placeholder="Type a message..."
  rows="1"
></textarea>
```

**Step 5: Add the mention dropdown HTML above the textarea in `index.html`**

Add right before the `<!-- Input container -->` div (around line 309):

```html
<!-- Mention autocomplete dropdown -->
<div x-show="mentionActive && mentionResults.length > 0" x-cloak
     class="bg-base-100 border border-base-300 rounded-lg shadow-lg mb-1 max-h-48 overflow-y-auto z-50">
  <template x-for="(user, idx) in mentionResults" :key="user.username">
    <button
      class="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-base-200 transition-colors text-left"
      :class="idx === mentionHighlight ? 'bg-base-200' : ''"
      @mousedown.prevent="insertMention(user)"
      @mouseenter="mentionHighlight = idx"
    >
      <template x-if="user.username === 'everyone'">
        <div class="avatar placeholder">
          <div class="bg-warning text-warning-content w-7 h-7 rounded-full flex items-center justify-center">
            <i data-lucide="users" class="w-3.5 h-3.5"></i>
          </div>
        </div>
      </template>
      <template x-if="user.username !== 'everyone'">
        <div x-html="window.userAvatarHtml(user.id, user.username, 'w-7 h-7 text-[0.6rem]', { presence: false })"></div>
      </template>
      <div class="flex-1 min-w-0">
        <span class="font-medium" x-text="user.username === 'everyone' ? '@everyone' : '@' + user.username"></span>
        <span x-show="user.username !== 'everyone'" class="text-base-content/50 ml-1 text-xs"
              x-text="[user.first_name, user.last_name].filter(Boolean).join(' ')"></span>
      </div>
    </button>
  </template>
</div>
```

**Step 6: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/js/chat.js workspace/chat/ui/templates/chat/ui/index.html
git commit -m "feat(chat): add @mention autocomplete dropdown in message textarea"
```

---

### Task 4: CSS — Mention badge styling

**Files:**
- Modify: `workspace/chat/ui/static/chat/ui/css/chat.css`

**Step 1: Add mention badge styles**

Append to `workspace/chat/ui/static/chat/ui/css/chat.css`:

```css
/* ── @mention badges ────────────────────────────────────── */
.mention-badge {
  background-color: oklch(var(--in) / 0.15);
  color: oklch(var(--in));
  padding: 1px 4px;
  border-radius: 4px;
  font-weight: 500;
}

.mention-everyone {
  background-color: oklch(var(--wa) / 0.15);
  color: oklch(var(--wa));
}
```

**Step 2: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/css/chat.css
git commit -m "feat(chat): add CSS styles for @mention badges"
```

---

### Task 5: Integration — Wire everything together and test

**Files:**
- Modify: `workspace/chat/views.py` (MessageDetailView.patch for edit)

**Step 1: Verify the edit message flow also renders mentions**

In `workspace/chat/views.py`, find `MessageDetailView.patch()` and update the `render_message_body(body)` call to also resolve valid usernames:

```python
# In the patch method, after getting the new body:
from .services import extract_mentions
raw_mentions, _ = extract_mentions(body)
valid_usernames = set()
if raw_mentions:
    valid_usernames = set(
        User.objects.filter(username__in=raw_mentions).values_list('username', flat=True)
    )
    valid_usernames.add('everyone')
body_html = render_message_body(body, valid_usernames=valid_usernames)
```

**Step 2: Verify `render_message_body` import in views.py includes the new function**

Check that the import at the top of `views.py` also imports `extract_mentions`:

```python
from .services import (
    extract_mentions,
    get_or_create_dm,
    get_unread_counts,
    notify_conversation_members,
    notify_new_message,
    render_message_body,
)
```

**Step 3: Run the dev server and manually test**

```bash
python manage.py runserver
```

Test cases:
1. Type `@` in a group chat → dropdown appears with members + @everyone
2. Type `@ab` → dropdown filters to matching members
3. Arrow keys navigate, Enter/Tab selects, Escape closes
4. Sent message shows `@username` as a colored badge
5. Mentioned user receives a notification (check notification bell)
6. Edit a message with `@username` → badge still renders after save

**Step 4: Commit**

```bash
git add workspace/chat/views.py
git commit -m "feat(chat): render mentions in edited messages"
```

---

### Task 6: Keyboard shortcut help update

**Files:**
- Modify: `workspace/chat/ui/templates/chat/ui/index.html`

**Step 1: Add @mention to keyboard shortcuts help popover**

In the keyboard shortcuts grid (around line 338-348), add:

```html
<span>Mention user</span>    <kbd class="kbd kbd-xs">@</kbd>
```

**Step 2: Commit**

```bash
git add workspace/chat/ui/templates/chat/ui/index.html
git commit -m "feat(chat): add @mention to keyboard shortcuts help"
```
