// Voice room Alpine app. Reuses the chat mixins (messages, input, SSE, members,
// panels, bot, call) but is locked to a single conversation and owns the call.
// No sidebar, no conversation list: the room is one conversation, full screen.
function chatRoomApp(currentUserId, conversationId) {
  return {
    currentUserId: currentUserId,
    roomConversationId: conversationId,
    callRole: 'owner',
    roomParticipants: [],
    chatPrefs: { ...(window._chatPrefsCache || {}) },

    ...chatMessagesMixin(),
    ...chatInputMixin(),
    ...chatSseMixin(),
    ...chatMembersMixin(),
    ...chatPanelsMixin(),
    ...chatBotMixin(),
    ...chatCallMixin(),

    async init() {
      this._initCallSounds?.();

      // Lock the app to this conversation (no list, no switching). Seed
      // activeConversation so every mixin that reads it targets the room call.
      this.activeConversation = { uuid: this.roomConversationId };

      // Seed the participants grid from the embedded server data.
      const seedEl = document.getElementById('room-participants-data');
      if (seedEl) {
        try { this.roomParticipants = JSON.parse(seedEl.textContent); }
        catch (e) { this.roomParticipants = []; }
      }

      // Leave cleanly when the tab closes (existing beacon).
      window.addEventListener('pagehide', () => { if (this.inCall) this._leaveBeacon?.(); });

      // Load the conversation messages, then auto-join the call.
      await this.loadMessages(this.roomConversationId);
      await this.startOrJoinCall();
    },
  };
}

window.chatRoomApp = chatRoomApp;
