# Emoji Picker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hardcoded 6-emoji quick pickers with `emoji-picker-element` web component for both message input and reactions, themed to match DaisyUI.

**Architecture:** A singleton `<emoji-picker>` element is positioned dynamically via JS depending on context (input or reaction). Quick emoji shortcuts are kept in the message hover toolbar for fast reactions.

**Tech Stack:** emoji-picker-element (CDN), Alpine.js, DaisyUI/Tailwind CSS variables

---

### Task 1: Add DaisyUI theming for emoji-picker

**Files:**
- Modify: `workspace/chat/ui/static/chat/ui/css/chat.css`

**Step 1: Add CSS custom properties to theme emoji-picker**

Append to the end of `chat.css`:

```css
/* ── Emoji picker: DaisyUI theme integration ────────────── */
emoji-picker {
  --background: oklch(var(--b1));
  --border-color: oklch(var(--b3));
  --border-radius: 0.75rem;
  --button-active-background: oklch(var(--b3));
  --button-hover-background: oklch(var(--b2));
  --indicator-color: oklch(var(--in));
  --input-border-color: oklch(var(--b3));
  --input-font-color: oklch(var(--bc));
  --input-placeholder-color: oklch(var(--bc) / 0.4);
  --outline-color: oklch(var(--in));
  --category-font-color: oklch(var(--bc) / 0.6);
  --num-columns: 8;
  --emoji-padding: 0.4rem;
  --emoji-size: 1.4rem;
  height: 20rem;
}
```

**Step 2: Verify visually**

Open the chat page in the browser, the picker won't be visible yet but the CSS is ready.

**Step 3: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/css/chat.css
git commit -m "feat(chat): add DaisyUI-themed CSS for emoji-picker-element"
```

---

### Task 2: Add CDN import and singleton picker element

**Files:**
- Modify: `workspace/chat/ui/templates/chat/ui/index.html`

**Step 1: Add CDN script import**

In the `{% block extra_head %}` section, after the cropper CSS link, add the emoji-picker-element module script:

```html
<script type="module" src="https://cdn.jsdelivr.net/npm/emoji-picker-element@^1/index.js"></script>
```

**Step 2: Add singleton `<emoji-picker>` element**

Right before the `<!-- Context menu -->` comment (after the closing `</div>` of the main content area, around line 413), add:

```html
<!-- Singleton emoji picker -->
<div x-show="emojiPickerVisible" x-cloak
     class="fixed z-[100]"
     :style="`left:${emojiPickerX}px; top:${emojiPickerY}px`"
     @click.outside="closeEmojiPicker()">
  <emoji-picker x-ref="emojiPicker"></emoji-picker>
</div>
```

**Step 3: Commit**

```bash
git add workspace/chat/ui/templates/chat/ui/index.html
git commit -m "feat(chat): add emoji-picker-element CDN import and singleton element"
```

---

### Task 3: Add emoji picker JS logic to chatApp

**Files:**
- Modify: `workspace/chat/ui/static/chat/ui/js/chat.js`

**Step 1: Add state properties**

After the `dragOverPinned: null,` line (around line 57), add:

```javascript
// Emoji picker
emojiPickerVisible: false,
emojiPickerMode: null,       // 'input' | 'reaction'
emojiPickerTargetMsg: null,  // message UUID for reaction mode
emojiPickerX: 0,
emojiPickerY: 0,
```

**Step 2: Add init hook for emoji-click listener**

In the `init()` method, after `this.$nextTick(() => { ... lucide.createIcons() ... });` (around line 121), add:

```javascript
// Emoji picker event listener
this.$nextTick(() => {
  const picker = this.$refs.emojiPicker;
  if (picker) {
    picker.addEventListener('emoji-click', (e) => {
      const unicode = e.detail.unicode;
      if (this.emojiPickerMode === 'input') {
        this.insertEmoji(unicode);
      } else if (this.emojiPickerMode === 'reaction' && this.emojiPickerTargetMsg) {
        this.toggleReaction(this.emojiPickerTargetMsg, unicode);
      }
      this.closeEmojiPicker();
    });
  }
});
```

**Step 3: Add openEmojiPicker and closeEmojiPicker methods**

After the `insertEmoji()` method (after line 1785), add:

```javascript
// ── Emoji picker ──────────────────────────────────────
openEmojiPicker(mode, event, msgUuid) {
  if (this.emojiPickerVisible && this.emojiPickerMode === mode && this.emojiPickerTargetMsg === msgUuid) {
    this.closeEmojiPicker();
    return;
  }

  this.emojiPickerMode = mode;
  this.emojiPickerTargetMsg = msgUuid || null;

  // Position relative to the trigger button
  const btn = event.currentTarget;
  const rect = btn.getBoundingClientRect();
  const pickerWidth = 320;
  const pickerHeight = 340;

  let x = rect.left;
  let y;

  if (mode === 'input') {
    // Open above the button
    y = rect.top - pickerHeight - 8;
  } else {
    // Open below the hover toolbar
    y = rect.bottom + 8;
  }

  // Keep within viewport
  if (x + pickerWidth > window.innerWidth) {
    x = window.innerWidth - pickerWidth - 8;
  }
  if (x < 8) x = 8;
  if (y < 8) {
    y = rect.bottom + 8;
  }
  if (y + pickerHeight > window.innerHeight) {
    y = rect.top - pickerHeight - 8;
  }

  this.emojiPickerX = x;
  this.emojiPickerY = y;
  this.emojiPickerVisible = true;
},

closeEmojiPicker() {
  this.emojiPickerVisible = false;
  this.emojiPickerMode = null;
  this.emojiPickerTargetMsg = null;
},
```

**Step 4: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/js/chat.js
git commit -m "feat(chat): add emoji picker open/close logic and emoji-click handler"
```

---

### Task 4: Wire up the input toolbar emoji button

**Files:**
- Modify: `workspace/chat/ui/templates/chat/ui/index.html`

**Step 1: Replace the input toolbar emoji dropdown**

Find the `<!-- Emoji picker -->` section in the input toolbar (lines 326-343). Replace the entire dropdown with:

```html
<!-- Emoji picker -->
<button class="btn btn-ghost btn-xs btn-square" title="Emoji" @click="openEmojiPicker('input', $event)">
  <i data-lucide="smile" class="w-3.5 h-3.5"></i>
</button>
```

**Step 2: Commit**

```bash
git add workspace/chat/ui/templates/chat/ui/index.html
git commit -m "feat(chat): wire input toolbar emoji button to emoji-picker-element"
```

---

### Task 5: Wire up the message hover toolbar emoji button

**Files:**
- Modify: `workspace/chat/ui/templates/chat/ui/partials/message_group.html`

**Step 1: Update the reaction picker in hover toolbar**

Find the `{# Reaction picker #}` section (lines 117-131). Replace it with:

```html
{# Quick reactions #}
<template x-for="emoji in quickEmojis" :key="emoji">
  <button class="btn btn-ghost btn-xs btn-circle text-base"
          @click="toggleReaction('{{ msg.uuid }}', emoji)"
          x-text="emoji"></button>
</template>
<div class="w-px self-stretch my-0.5 bg-base-300"></div>
{# Full emoji picker #}
<button class="btn btn-ghost btn-xs btn-circle"
        @click="openEmojiPicker('reaction', $event, '{{ msg.uuid }}')"
        title="More reactions">
  <i data-lucide="smile-plus" class="w-3.5 h-3.5"></i>
</button>
```

This removes the `x-data="{ emojiOpen: false }"` from the parent div since we no longer need local state.

**Step 2: Remove the local `x-data` from hover toolbar container**

The parent div at line 114 currently has `x-data="{ emojiOpen: false }"`. Remove that attribute since it's no longer needed. Change:

```html
x-data="{ emojiOpen: false }">
```

to just close the existing attributes without `x-data`.

**Step 3: Commit**

```bash
git add workspace/chat/ui/templates/chat/ui/partials/message_group.html
git commit -m "feat(chat): show quick emojis + full picker button in message hover toolbar"
```

---

### Task 6: Close emoji picker on Escape key

**Files:**
- Modify: `workspace/chat/ui/static/chat/ui/js/chat.js`

**Step 1: Add Escape handler**

In the window `@keydown.window` handler in `index.html` (line 30-34), or in `handleInputKeydown()` in chat.js. Best to add it in the existing `handleInputKeydown` Escape block. Update the Escape handler to also close the emoji picker.

In `handleInputKeydown`, before the existing Escape handling (line 1799), add:

```javascript
if (e.key === 'Escape' && this.emojiPickerVisible) {
  this.closeEmojiPicker();
  return;
}
```

**Step 2: Commit**

```bash
git add workspace/chat/ui/static/chat/ui/js/chat.js
git commit -m "feat(chat): close emoji picker on Escape key"
```

---

### Task 7: Final integration test

**Step 1: Run Django dev server and test manually**

Run: `python manage.py runserver`

Test checklist:
- [ ] Open a chat conversation
- [ ] Click the smile icon in the input toolbar → full emoji picker opens above
- [ ] Select an emoji → inserts into the message textarea at cursor position
- [ ] Hover a message → quick emoji buttons (6) visible inline
- [ ] Click a quick emoji → reaction toggles on the message
- [ ] Click smile-plus on hover toolbar → full emoji picker opens below
- [ ] Select an emoji → reaction toggled on that message
- [ ] Press Escape → picker closes
- [ ] Click outside picker → picker closes
- [ ] Switch DaisyUI theme → picker colors update correctly

**Step 2: Final commit**

```bash
git add -A
git commit -m "feat(chat): integrate emoji-picker-element for input and reactions"
```
