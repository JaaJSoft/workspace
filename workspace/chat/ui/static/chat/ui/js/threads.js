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
      if (this.openThreadRoot && this.openThreadRoot !== rootUuid) {
        // x-if only tears down through a falsy value - a root that merely
        // changes never unmounts the panel, which would keep showing the
        // thread it was constructed for. Bounce through null so Alpine
        // rebuilds the component around the new root.
        this.openThreadRoot = null;
        this.$nextTick(() => { this.openThreadRoot = rootUuid; });
      } else {
        this.openThreadRoot = rootUuid;
      }
      this.markThreadRead(rootUuid);
    },

    closeThread() {
      this.openThreadRoot = null;
    },

    async markThreadRead(rootUuid) {
      this.threadUnreadCounts = { ...this.threadUnreadCounts, [rootUuid]: 0 };
      // Always POST, even when the local counter reads zero: it only ever
      // holds what this page session saw arrive over SSE, and starts empty on
      // load. A thread with a backlog from before the page opened would
      // otherwise never be cleared server-side, and its badge would survive
      // every visit.
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
  // Kept before the spread so the override below can still reach the shared
  // implementation: object spread copies values, leaving no `super` to call.
  const messages = chatMessagesMixin();

  return {
    threadRootUuid: rootUuid,

    ...chatUiHelpersMixin(),
    ...messages,
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
    // A thread load also carries the root message, rendered outside the
    // paginated list so "load older" cannot sink it below older replies.
    _loadTargets() { return ['thread-root-message', this._messageListId()]; },
    // Writing in the panel with nothing quoted answers the thread itself.
    _replyTarget() { return this.replyingTo?.uuid || this.threadRootUuid; },

    // Set by destroy(). Responses in flight die with the panel's DOM
    // (alpine-ajax skips targets that left the document), but this component
    // can still be resumed by an awaited chain after teardown - opening
    // thread B while thread A still loads keeps the same conversation and
    // the same target ids, so a request issued by A's dead component would
    // merge A's thread into B's panel. The flag stops it before it is sent.
    _dead: false,
    _surfaceGone() { return this._dead; },

    async _refreshCurrentMessages() {
      await messages._refreshCurrentMessages.call(this);
      // Then the conversation behind the panel: its copy of the root carries
      // the reply count, and with the inline preference on it lists the
      // replies too. Neither updates on its own, because SSE never echoes your
      // own message back to you - so without this, writing in the panel needs
      // a page reload to show up in the flow.
      window.dispatchEvent(new CustomEvent('chat:refresh-messages'));
    },

    // A live reply to the thread on screen: reload the panel, then animate
    // the bubble the reload inserted, like the main flow does for its own
    // new messages. Replies to other threads are the flow's business.
    async onReplyReceived(detail) {
      if (detail?.root !== this.threadRootUuid) return;
      await this.loadMessages();
      this._animateMessageEntry(detail.uuid);
    },

    // The panel-only repaint, without the chat:refresh-messages echo above.
    // Used by the chat:refresh-thread listener: a reaction toggled in the main
    // flow already repainted the flow, so echoing back would fetch it twice.
    refreshPanelOnly() { return messages._refreshCurrentMessages.call(this); },

    // No-op on purpose: the panel's _refreshCurrentMessages already tells the
    // main flow to repaint, and dispatching chat:refresh-thread from the
    // panel would just make it fetch its own contents a second time.
    _notifyReactionPeers() {},

    async init() {
      this.initRecorder?.();
      // Take focus so the panel's own Escape binding is reachable from the
      // keyboard, which matters most on mobile where it covers the page.
      this.$nextTick(() => this.$el?.focus?.());
      await this.loadMessages();
      // The root has to belong to the conversation the panel sits in. The
      // server enforces membership, not coherence: a crafted
      // /chat/<A>?thread=<root of B> deep link loads fine as long as the user
      // is a member of B - and would show B's thread inside A, with a
      // composer that posts into A and 400s. The server stamps the thread's
      // conversation on the list; on mismatch, close rather than mislead.
      const list = document.getElementById(this._messageListId());
      const owner = list?.dataset.conversationUuid;
      if (owner && owner !== this.activeConversation.uuid) {
        this.closeThread?.();
      }
    },

    // Alpine calls this when x-if tears the panel down - closing the thread,
    // opening another one, or switching to the info panel. A recording in
    // flight holds a live microphone track and an object URL; without this
    // they survive the component and the browser keeps showing the recording
    // indicator. cancelRecording() already releases both. _dead makes every
    // fetch still in flight drop its response instead of writing into
    // whatever panel now owns the container.
    destroy() {
      this._dead = true;
      this._cancelMessagesRetry?.();
      this.cancelRecording?.();
    },
  };
};

window.chatThreadRouteTargets = chatThreadRouteTargets;
