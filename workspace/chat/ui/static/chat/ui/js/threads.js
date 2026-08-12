// Threads: the side panel that reads and writes one thread, plus the routing
// that decides where an incoming message belongs.
//
// The panel is a second instance of the conversation mixins on the same page,
// the way the voice room is a second instance on its own page. Everything that
// differs between the two surfaces goes through the hooks chatMessagesMixin
// exposes, so no message-loading logic is duplicated here.

/**
 * Where an incoming SSE message should land.
 *
 * Pure: no DOM, no component state mutated, so the routing rules are testable
 * on their own. `bumpRoot` is the uuid of a root whose reply counter should
 * grow, or null when the message is not a reply.
 *
 * @param {{message: {uuid: string, thread_root: string|null}}} detail
 * @param {{openThreadRoot: string|null, showInline: boolean}} state
 * @returns {{mainFlow: boolean, panel: boolean, bumpRoot: string|null}}
 */
function chatThreadRouteTargets(detail, state) {
  const root = detail?.message?.thread_root || null;
  if (!root) {
    return { mainFlow: true, panel: false, bumpRoot: null };
  }
  return {
    mainFlow: !!state.showInline,
    panel: state.openThreadRoot === root,
    bumpRoot: root,
  };
}

window.chatThreadsMixin = function chatThreadsMixin() {
  return {
    // uuid of the root whose thread the panel is showing, or null when closed.
    openThreadRoot: null,
    // root uuid -> unread replies, kept live by the SSE router.
    threadUnreadCounts: {},

    // A method, not a getter: this object is spread into chatApp, and spread
    // copies values, so a getter would be evaluated once and frozen.
    threadUnread(rootUuid) {
      return this.threadUnreadCounts[rootUuid] || 0;
    },

    openThread(rootUuid) {
      // The thread panel is wider than the info and search panels and would
      // fight them for the same column, so opening one closes the others.
      this.showInfoPanel = false;
      this.closeSearchPanel?.();
      this.openThreadRoot = rootUuid;
      this.markThreadRead(rootUuid);
    },

    closeThread() {
      this.openThreadRoot = null;
    },

    async markThreadRead(rootUuid) {
      const backlog = this.threadUnreadCounts[rootUuid] || 0;
      this.threadUnreadCounts = { ...this.threadUnreadCounts, [rootUuid]: 0 };
      if (!backlog) return;
      try {
        await fetch(`/api/v1/chat/threads/${rootUuid}/read`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
      } catch (e) {
        console.error('Failed to mark thread read', e);
      }
    },

    bumpThreadUnread(rootUuid) {
      if (!rootUuid || this.openThreadRoot === rootUuid) return;
      const current = this.threadUnreadCounts[rootUuid] || 0;
      this.threadUnreadCounts = { ...this.threadUnreadCounts, [rootUuid]: current + 1 };
    },

    _bumpRenderedReplyCount(rootUuid) {
      // Keyed by uuid rather than found by walking siblings: a message group
      // holds several messages, so a positional lookup would land on a
      // neighbour's counter. Every rendered copy is updated, since the root can
      // be on screen in both the main flow and a panel.
      //
      // A root that was never paginated in has no counter here, and that is
      // correct: the count is rendered server-side next time the page loads.
      document
        .querySelectorAll(`[data-thread-count="${rootUuid}"]`)
        .forEach((label) => {
          const next = (parseInt(label.textContent, 10) || 0) + 1;
          label.textContent = `${next} ${next === 1 ? 'reply' : 'replies'}`;
        });
    },
  };
};

window.chatThreadPanel = function chatThreadPanel(rootUuid) {
  return {
    threadRootUuid: rootUuid,

    ...chatUiHelpersMixin(),
    ...chatMessagesMixin(),
    ...chatInputMixin(),
    ...chatRecorderMixin(),

    // Surface hooks: every DOM id and endpoint the messages mixin touches is
    // redirected at the panel, so the two instances on the page never collide.
    _messagesContainerId() { return 'thread-messages-container'; },
    _messageListId() { return 'thread-message-list'; },
    _messageIdPrefix() { return 'tmsg'; },
    _messagesUrl(cursor) {
      const base = `/chat/threads/${this.threadRootUuid}/messages`;
      return cursor ? `${base}?before=${cursor}` : base;
    },
    // Writing in the panel with nothing quoted answers the thread itself.
    _replyTarget() { return this.replyingTo?.uuid || this.threadRootUuid; },

    async init() {
      this.initRecorder?.();
      await this.loadMessages(this.activeConversation.uuid);
    },
  };
};

window.chatThreadRouteTargets = chatThreadRouteTargets;
