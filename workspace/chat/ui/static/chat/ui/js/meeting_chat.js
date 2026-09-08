// The meeting's own chat: a JSON list rendered client-side through the
// <chat-message-group> shell, for hosts and guests alike. Delivery is the
// mailbox: a meeting_message event appends, a meeting_message_deleted removes,
// and a reconnect reloads the whole list.

const CHAT_MEETING_GROUP_WINDOW_MS = 5 * 60 * 1000;

/**
 * Group a chronological message list for rendering.
 * @param {Array<object>} messages
 * @param {?string} ownKey the reader's participant key
 * @returns {Array<{type:'divider',label:string}|{type:'group',author:object,own:boolean,items:Array<object>}>}
 */
function chatMeetingGroupMessages(messages, ownKey) {
  const rows = [];
  let lastOccurrence;
  let lastDay = null;
  let group = null;
  for (const m of messages || []) {
    const occurrence = m.occurrence_start || null;
    const day = (m.created_at || '').slice(0, 10);
    if (occurrence !== lastOccurrence || (occurrence === null && day !== lastDay)) {
      const stamp = occurrence || m.created_at;
      const d = new Date(stamp);
      rows.push({
        type: 'divider',
        label: isNaN(d) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: occurrence ? 'short' : undefined }),
      });
      lastOccurrence = occurrence;
      lastDay = day;
      group = null;
    }
    const key = m.author && m.author.participant_key;
    const prev = group && group.items[group.items.length - 1];
    const close = prev && (new Date(m.created_at) - new Date(prev.created_at)) < CHAT_MEETING_GROUP_WINDOW_MS;
    if (group && group.author.participant_key === key && close) {
      group.items.push(m);
    } else {
      group = { type: 'group', author: m.author, own: !!ownKey && key === ownKey, items: [m] };
      rows.push(group);
    }
  }
  return rows;
}

window.chatMeetingChatMixin = function chatMeetingChatMixin() {
  return {
    meetingMessages: [],
    meetingChatLoading: false,
    meetingHasMore: false,
    messageBody: '',

    // -- Transport seam: the host page and the guest page override these --
    _meetingMessagesUrl(cursor) { return cursor ? `?before=${cursor}` : ''; },
    _meetingMessageHeaders() { return {}; },
    _canDeleteMeetingMessages() { return false; },
    // Who is LOOKING: a guest holds a meeting token, so the avatar must not
    // ask for presence or open a profile card (see message_shell.js).
    _viewerIsGuest() { return false; },

    // Looked up by id, not through $refs: Alpine scopes every x-if clone as a
    // root of its own, and both $refs and $root resolve against whichever
    // element the expression that reached this method was bound to. A frame
    // arriving on the guest's stream carries the `this` from the name-phase
    // form, whose branch is long gone - so a ref lookup finds nothing and the
    // pane silently stops re-rendering. The pane is a singleton per page, so
    // an id is the addressing that holds whatever mounted it.
    _meetingChatEl(id) { return document.getElementById(id); },

    getMessageInput() { return this._meetingChatEl('meeting-chat-input'); },
    autoResize(el) {
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
    },

    async loadMeetingMessages() {
      this.meetingChatLoading = true;
      try {
        const resp = await fetch(this._meetingMessagesUrl(null), { headers: this._meetingMessageHeaders() });
        if (!resp.ok) return;
        const data = await resp.json();
        this.meetingMessages = data.messages || [];
        this.meetingHasMore = !!data.has_more;
        this.renderMeetingMessages();
        this.scrollMeetingChatToBottom();
      } catch (e) { /* the list stays as it was; the next event reloads it */ }
      finally { this.meetingChatLoading = false; }
    },

    async loadOlderMeetingMessages() {
      const first = this.meetingMessages[0];
      if (!first || this.meetingChatLoading) return;
      this.meetingChatLoading = true;
      try {
        const resp = await fetch(this._meetingMessagesUrl(first.uuid), { headers: this._meetingMessageHeaders() });
        if (!resp.ok) return;
        const data = await resp.json();
        this.meetingMessages = [...(data.messages || []), ...this.meetingMessages];
        this.meetingHasMore = !!data.has_more;
        this.renderMeetingMessages();
      } finally { this.meetingChatLoading = false; }
    },

    async sendMeetingMessage() {
      const body = (this.messageBody || '').trim();
      if (!body) return;
      this.messageBody = '';
      this.autoResize(this.getMessageInput());
      let resp;
      try {
        resp = await fetch(this._meetingMessagesUrl(null), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this._meetingMessageHeaders() },
          body: JSON.stringify({ body }),
        });
      } catch (e) { resp = null; }
      if (!resp || !resp.ok) {
        this.messageBody = body;
        window.AppAlert?.error('Your message was not sent.');
        return;
      }
      this._appendMeetingMessage(await resp.json());
    },

    handleMeetingKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMeetingMessage(); return; }
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === 'b') { e.preventDefault(); this.wrapSelection('**'); }
      else if (mod && e.key === 'i') { e.preventDefault(); this.wrapSelection('*'); }
      else if (mod && e.key === 'e') { e.preventDefault(); this.wrapSelection('`'); }
      else if (e.key === 'Escape' && this.emojiPickerVisible) { this.closeEmojiPicker(); }
    },

    onMeetingMessage(detail) {
      if (!detail || !detail.message) return;
      this._appendMeetingMessage(detail.message);
      this._onMeetingMessageArrived?.(detail.message);
    },

    onMeetingMessageDeleted(detail) {
      if (!detail || !detail.message_id) return;
      this.meetingMessages = this.meetingMessages.filter((m) => m.uuid !== detail.message_id);
      this.renderMeetingMessages();
    },

    async deleteMeetingMessage(uuid) {
      const resp = await fetch(`${this._meetingMessagesUrl(null)}/${uuid}`, {
        method: 'DELETE', headers: this._meetingMessageHeaders(),
      });
      if (resp.ok) this.onMeetingMessageDeleted({ message_id: uuid });
    },

    _appendMeetingMessage(message) {
      if (this.meetingMessages.some((m) => m.uuid === message.uuid)) return;
      const atBottom = this._meetingChatNearBottom();
      this.meetingMessages = [...this.meetingMessages, message];
      this.renderMeetingMessages();
      if (atBottom) this.scrollMeetingChatToBottom();
    },

    _meetingChatNearBottom() {
      const el = this._meetingChatEl('meeting-chat-scroll');
      return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    },

    scrollMeetingChatToBottom() {
      this.$nextTick(() => {
        const el = this._meetingChatEl('meeting-chat-scroll');
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    // Builds the DOM the shell element expects (see message_shell.js): one
    // <chat-message-group> per group, one slot="message" bubble per line.
    renderMeetingMessages() {
      const list = this._meetingChatEl('meeting-chat-list');
      if (!list) return;
      const rows = chatMeetingGroupMessages(this.meetingMessages, this.currentParticipantKey);
      const viewerIsGuest = this._viewerIsGuest();
      const frag = document.createDocumentFragment();
      for (const row of rows) {
        if (row.type === 'divider') {
          const div = document.createElement('div');
          div.className = 'divider text-xs text-base-content/50 my-3';
          div.textContent = row.label;
          frag.appendChild(div);
          continue;
        }
        const group = document.createElement('chat-message-group');
        if (row.own) group.setAttribute('own', '');
        if (row.author.is_guest) group.setAttribute('guest', '');
        if (viewerIsGuest) group.setAttribute('viewer-guest', '');
        if (row.author.id != null) group.setAttribute('author-id', row.author.id);
        group.setAttribute('author-username', row.author.username || '');
        group.setAttribute('author-name', row.author.display_name || row.author.username || '');
        group.setAttribute('id-prefix', 'mmsg');
        for (const m of row.items) {
          const bubble = document.createElement('div');
          bubble.setAttribute('slot', 'message');
          bubble.id = `mmsg-${m.uuid}`;
          bubble.setAttribute('data-message-uuid', m.uuid);
          bubble.setAttribute('data-has-body', '');
          const body = document.createElement('div');
          body.className = 'msg-body prose prose-sm max-w-[36rem] break-words';
          body.innerHTML = m.body_html;
          bubble.appendChild(body);
          if (this._canDeleteMeetingMessages()) {
            const tools = document.createElement('div');
            tools.setAttribute('data-part', 'after-bubble');
            tools.className = 'absolute -top-3 right-0 hidden group-hover/msg:flex bg-base-100 border border-base-300 rounded-lg shadow-sm';
            const btn = document.createElement('button');
            btn.className = 'btn btn-ghost btn-xs btn-square text-error';
            btn.title = 'Delete';
            // A listener, not an x-on attribute: the shell element rebuilds
            // this subtree on upgrade, so the binding has to survive without
            // depending on when Alpine walks it.
            btn.addEventListener('click', () => this.deleteMeetingMessage(m.uuid));
            btn.innerHTML = '<i data-lucide="trash-2" class="w-3.5 h-3.5"></i>';
            tools.appendChild(btn);
            bubble.appendChild(tools);
          }
          group.appendChild(bubble);
        }
        const footer = document.createElement('span');
        footer.setAttribute('slot', 'footer');
        footer.className = 'text-[0.65rem] opacity-50';
        const last = new Date(row.items[row.items.length - 1].created_at);
        footer.textContent = isNaN(last) ? '' : last.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        group.appendChild(footer);
        frag.appendChild(group);
      }
      list.replaceChildren(frag);
      if (window.Alpine) window.Alpine.initTree(list);
      if (window.lucide) window.lucide.createIcons({ nodes: list.querySelectorAll('[data-lucide]') });
    },
  };
};

window.chatMeetingGroupMessages = chatMeetingGroupMessages;
