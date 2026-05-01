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
      let attempts = 0;
      const maxAttempts = 30; // 60s max
      const poll = async () => {
        if (++attempts > maxAttempts) {
          this.aiSummarizing = false;
          clearInterval(this._aiPollInterval);
          AppDialog.error({ message: 'AI task timed out' });
          return;
        }
        try {
          const resp = await fetch(`/api/v1/ai/tasks/${taskId}`);
          if (!resp.ok) return;
          const task = await resp.json();
          if (task.status === 'completed') {
            this.aiSummary = task.result_html || task.result;
            this.aiSummarizing = false;
            clearInterval(this._aiPollInterval);

          } else if (task.status === 'failed') {
            AppDialog.error({ message: task.error || 'AI task failed' });
            this.aiSummarizing = false;
            clearInterval(this._aiPollInterval);
          }
        } catch (e) {
          // silent retry
        }
      };
      this._aiPollInterval = setInterval(poll, 2000);
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
      let attempts = 0;
      const maxAttempts = 30; // 60s max
      const poll = async () => {
        if (++attempts > maxAttempts) {
          this.aiComposing = false;
          clearInterval(this._aiComposePollInterval);
          AppDialog.error({ message: 'AI composition timed out' });
          return;
        }
        try {
          const resp = await fetch(`/api/v1/ai/tasks/${taskId}`);
          if (!resp.ok) return;
          const task = await resp.json();
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
            clearInterval(this._aiComposePollInterval);
          } else if (task.status === 'failed') {
            AppDialog.error({ message: task.error || 'AI composition failed' });
            this.aiComposing = false;
            clearInterval(this._aiComposePollInterval);
          }
        } catch (e) {
          // silent retry
        }
      };
      this._aiComposePollInterval = setInterval(poll, 2000);
      poll();
    },
  };
};
