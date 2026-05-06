// Mail AI features: summarize a message, dismiss summary, AI-assisted compose,
// background polling for AI tasks.
window.mailAiMixin = function mailAiMixin() {
  return {
    // ----- AI features -----
    async summarizeMessage(message) {
      this.aiSummarizing = true;
      this.aiSummary = null;
      try {
        const resp = await fetch('/api/v1/ai/tasks/mail/summarize', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify({ message_id: message.uuid }),
        });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || 'Failed to start summarization');
        }
        const task = await resp.json();
        this._pollAITask(task.uuid);
      } catch (e) {
        this.aiSummarizing = false;
        AppDialog.error({ message: e.message });
      }
    },

    async _pollAITask(taskId) {
      // Supersede any in-flight poll from a previous summarize request so its
      // late-arriving result can't overwrite the current selection's summary.
      if (this._aiPollInterval) clearInterval(this._aiPollInterval);
      this._aiPollToken = (this._aiPollToken || 0) + 1;
      const token = this._aiPollToken;
      const isCurrent = () => this._aiPollToken === token;

      let intervalId = null;
      const stop = () => { if (intervalId) clearInterval(intervalId); };

      let attempts = 0;
      const maxAttempts = 30; // 60s max
      const poll = async () => {
        if (!isCurrent()) { stop(); return; }
        if (++attempts > maxAttempts) {
          stop();
          this.aiSummarizing = false;
          AppDialog.error({ message: 'AI task timed out' });
          return;
        }
        try {
          const resp = await fetch(`/api/v1/ai/tasks/${taskId}`);
          if (!isCurrent()) { stop(); return; }
          if (!resp.ok) return;
          const task = await resp.json();
          if (!isCurrent()) { stop(); return; }
          if (task.status === 'completed') {
            this.aiSummary = task.result_html || task.result;
            this.aiSummarizing = false;
            stop();
          } else if (task.status === 'failed') {
            AppDialog.error({ message: task.error || 'AI task failed' });
            this.aiSummarizing = false;
            stop();
          }
        } catch (e) {
          // silent retry
        }
      };
      intervalId = setInterval(poll, 2000);
      this._aiPollInterval = intervalId;
      poll();
    },

    async dismissSummary(message) {
      this.aiSummary = null;
      try {
        await fetch(`/api/v1/mail/messages/${message.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ ai_summary: '' }),
        });
      } catch (e) {
        // silent — summary already hidden locally
      }
    },

    async aiCompose() {
      if (!this.aiComposePrompt.trim()) return;
      this.aiComposing = true;

      const isReply = this.compose.is_reply && this.compose.reply_message_id;
      const endpoint = isReply ? '/api/v1/ai/tasks/mail/reply' : '/api/v1/ai/tasks/mail/compose';
      const body = isReply
        ? { message_id: this.compose.reply_message_id, instructions: this.aiComposePrompt }
        : { instructions: this.aiComposePrompt, context: this.compose.body, account_id: this.compose.account_id };

      try {
        const resp = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify(body),
        });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || 'Failed to start AI composition');
        }
        const task = await resp.json();
        this._pollAIComposeTask(task.uuid);
      } catch (e) {
        this.aiComposing = false;
        AppDialog.error({ message: e.message });
      }
    },

    async _pollAIComposeTask(taskId) {
      // Supersede any in-flight poll from a previous compose request so a
      // late-arriving result can't be prepended on top of a newer draft.
      if (this._aiComposePollInterval) clearInterval(this._aiComposePollInterval);
      this._aiComposePollToken = (this._aiComposePollToken || 0) + 1;
      const token = this._aiComposePollToken;
      const isCurrent = () => this._aiComposePollToken === token;

      let intervalId = null;
      const stop = () => { if (intervalId) clearInterval(intervalId); };

      let attempts = 0;
      const maxAttempts = 30; // 60s max
      const poll = async () => {
        if (!isCurrent()) { stop(); return; }
        if (++attempts > maxAttempts) {
          stop();
          this.aiComposing = false;
          AppDialog.error({ message: 'AI composition timed out' });
          return;
        }
        try {
          const resp = await fetch(`/api/v1/ai/tasks/${taskId}`);
          if (!isCurrent()) { stop(); return; }
          if (!resp.ok) return;
          const task = await resp.json();
          if (!isCurrent()) { stop(); return; }
          if (task.status === 'completed') {
            if (this.compose.is_reply && this.compose.body) {
              // Prepend AI response before the quoted original
              this.compose.body = task.result + '\n\n' + this.compose.body;
            } else {
              this.compose.body = task.result;
            }
            this.aiComposing = false;
            this.showAICompose = false;
            this.aiComposePrompt = '';
            stop();
          } else if (task.status === 'failed') {
            AppDialog.error({ message: task.error || 'AI composition failed' });
            this.aiComposing = false;
            stop();
          }
        } catch (e) {
          // silent retry
        }
      };
      intervalId = setInterval(poll, 2000);
      this._aiComposePollInterval = intervalId;
      poll();
    },
  };
};
