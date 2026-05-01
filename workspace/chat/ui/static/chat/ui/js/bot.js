// Bot picker, retry/cancel, memories and scheduled-message management.
window.chatBotMixin = function chatBotMixin() {
  return {
    showBotPicker: false,
    availableBots: [],
    botFilter: '',
    botTyping: false,

    botMemories: [],
    loadingBotMemories: false,
    memorySearch: '',

    scheduledMessages: [],
    loadingSchedules: false,

    async fetchBots() {
      try {
        const resp = await fetch('/api/v1/ai/bots', { credentials: 'same-origin' });
        if (resp.ok) this.availableBots = await resp.json();
      } catch (e) {
        // AI may not be enabled — silently ignore
      }
    },

    async startBotConversation(bot) {
      this.showBotPicker = false;
      try {
        const resp = await fetch('/api/v1/chat/conversations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({
            member_ids: [bot.user_id],
          }),
        });
        if (!resp.ok) throw new Error('Failed to create conversation');
        const conv = await resp.json();
        if (!this.conversations.find(c => c.uuid === conv.uuid)) {
          this.conversations.unshift(conv);
          this.refreshConversationList();
        }
        await this.selectConversation(conv);
      } catch (e) {
        console.error('Failed to start bot conversation', e);
      }
    },

    isBotConversation(conv) {
      if (!conv) return false;
      if (conv.is_bot_conversation) return true;
      if (!conv.members) return false;
      return conv.members.some(m => this.availableBots.some(b => b.user_id === m.user.id));
    },

    isBotMessage(msg) {
      return this.availableBots.some(b => b.user_id === msg.author?.id);
    },

    _getBotMember() {
      if (!this.activeConversation?.members) return null;
      return this.activeConversation.members.find(m =>
        this.availableBots.some(b => b.user_id === m.user.id)
      );
    },

    botTypingName() {
      const m = this._getBotMember();
      return m ? this.memberDisplayName(m) : 'AI';
    },

    async retryBotResponse(errorMsgUuid) {
      if (!this.activeConversation) return;
      const convId = this.activeConversation.uuid;

      // Remove the error message from the DOM immediately
      const el = document.getElementById(`msg-${errorMsgUuid}`);
      const group = el?.closest('.msg-group');
      if (group) group.remove();

      this.botTyping = true;
      try {
        const res = await fetch(`/api/v1/chat/conversations/${convId}/messages/${errorMsgUuid}/retry`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!res.ok) throw new Error('Retry failed');
      } catch (e) {
        console.error('Bot retry failed', e);
        this.botTyping = false;
        await this._refreshCurrentMessages();
      }
    },

    async cancelBotResponse() {
      if (!this.activeConversation) return;
      const convId = this.activeConversation.uuid;
      this.botTyping = false;
      try {
        await fetch(`/api/v1/chat/conversations/${convId}/bot-cancel`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
      } catch (e) {
        console.error('Bot cancel failed', e);
      }
    },

    botTypingAvatar() {
      const m = this._getBotMember();
      if (m) {
        return window.userAvatarHtml(m.user.id, m.user.username, 'w-8 h-8 text-xs', { presence: false });
      }
      return '<div class="w-8 h-8 rounded-full bg-secondary flex items-center justify-center"><i data-lucide="sparkles" class="w-4 h-4 text-secondary-content"></i></div>';
    },

    // ── Bot memories ──────────────────────────────────────────
    get filteredBotMemories() {
      if (!this.memorySearch) return this.botMemories;
      const q = this.memorySearch.toLowerCase();
      return this.botMemories.filter(m =>
        m.key.toLowerCase().includes(q) || m.content.toLowerCase().includes(q)
      );
    },

    async loadBotMemories() {
      const botMember = this._getBotMember();
      if (!botMember) return;
      this.loadingBotMemories = true;
      this.botMemories = [];
      try {
        const resp = await fetch(`/api/v1/ai/memories?bot_id=${botMember.user.id}`, {
          credentials: 'same-origin',
        });
        if (resp.ok) this.botMemories = await resp.json();
      } catch (e) {
        console.error('Failed to load bot memories', e);
      }
      this.loadingBotMemories = false;
    },

    async editMemory(mem) {
      const content = await AppDialog.prompt({
        title: 'Edit memory',
        message: mem.key,
        value: mem.content,
        placeholder: 'Memory content...',
        okLabel: 'Save',
        inputSize: 'textarea',
        icon: 'brain',
        iconClass: 'bg-secondary/10 text-secondary',
      });
      if (content === null || content.trim() === mem.content) return;
      const resp = await fetch(`/api/v1/ai/memories/${mem.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        credentials: 'same-origin',
        body: JSON.stringify({ content: content.trim() }),
      });
      if (resp.ok) {
        mem.content = content.trim();
      }
    },

    async deleteMemory(mem) {
      const ok = await AppDialog.confirm({
        title: 'Delete memory',
        message: `Delete memory "${mem.key}"?`,
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      const resp = await fetch(`/api/v1/ai/memories/${mem.id}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
        credentials: 'same-origin',
      });
      if (resp.ok) {
        this.botMemories = this.botMemories.filter(m => m.id !== mem.id);
      }
    },

    // ── Scheduled messages ──────────────────────────────────
    async loadScheduledMessages(conversationId) {
      if (!this.activeConversation || !this.isBotConversation(this.activeConversation)) return;
      this.loadingSchedules = true;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/schedules`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.scheduledMessages = await resp.json();
        }
      } catch (e) {
        console.error('Failed to load schedules', e);
      }
      this.loadingSchedules = false;
    },

    scheduleTimingLabel(sched) {
      if (sched.kind === 'once') {
        return 'One-time';
      }
      let label = `Every ${sched.recurrence_interval > 1 ? sched.recurrence_interval + ' ' : ''}${sched.recurrence_unit}`;
      if (sched.recurrence_time) {
        label += ` at ${sched.recurrence_time.slice(0, 5)}`;
      }
      return label;
    },

    async editSchedule(sched) {
      const prompt = await AppDialog.prompt({
        title: 'Edit scheduled message',
        message: 'Update the instruction for this schedule:',
        value: sched.prompt,
        placeholder: 'Instruction...',
        okLabel: 'Save',
        inputSize: 'textarea',
        icon: 'clock',
        iconClass: 'bg-info/10 text-info',
      });
      if (prompt === null || prompt.trim() === sched.prompt) return;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/schedules/${sched.uuid}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCSRFToken(),
            },
            credentials: 'same-origin',
            body: JSON.stringify({ prompt: prompt.trim() }),
          },
        );
        if (resp.ok) {
          const updated = await resp.json();
          const idx = this.scheduledMessages.findIndex(s => s.uuid === sched.uuid);
          if (idx !== -1) this.scheduledMessages[idx] = updated;
        }
      } catch (e) {
        console.error('Failed to edit schedule', e);
      }
    },

    async deleteSchedule(sched) {
      const ok = await AppDialog.confirm({
        title: 'Delete scheduled message',
        message: 'Delete this scheduled message?',
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/schedules/${sched.uuid}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() },
            credentials: 'same-origin',
          },
        );
        if (resp.ok) {
          this.scheduledMessages = this.scheduledMessages.filter(s => s.uuid !== sched.uuid);
        }
      } catch (e) {
        console.error('Failed to delete schedule', e);
      }
    },
  };
};
