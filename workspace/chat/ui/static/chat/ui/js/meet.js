// The public meeting page. One component, four phases: name -> lobby -> room
// -> over. Composes chatCallMixin through its transport seam so the WebRTC
// and presence code is the member room's, not a copy.
//
// The guest token never travels in a URL: it is sent as the X-Meeting-Token
// header, which is why the event stream below is a fetch() reader rather
// than an EventSource (which cannot set headers).
//
// "over" is a one-way door, and only meeting_ended, a refusal or a removal
// opens it. A call that ends is not the meeting ending - the host can start
// another one - so that leaves the guest in the room, waiting.

/**
 * Split a raw SSE buffer into complete frames plus the trailing partial one.
 * Pure: the reader owns the buffer, this owns the wire format.
 * @param {string} buffer
 * @returns {{frames: Array<{id: ?string, payload: object}>, rest: string}}
 */
function chatMeetParseSseChunk(buffer) {
  const frames = [];
  let rest = buffer;
  let sep = rest.indexOf('\n\n');
  while (sep !== -1) {
    const raw = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    sep = rest.indexOf('\n\n');
    if (raw.startsWith(':')) continue;
    let id = null;
    let data = '';
    for (const line of raw.split('\n')) {
      if (line.startsWith('id:')) id = line.slice(3).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) continue;
    try {
      frames.push({ id, payload: JSON.parse(data) });
    } catch (e) { /* malformed frame: skip it rather than kill the reader */ }
  }
  return { frames, rest };
}

/**
 * The initials a group conversation is drawn with, the way the server draws
 * them (chat/services/avatar.py, _name_initials): the first letter of the
 * first two parts, split on commas when the name lists several people and on
 * whitespace otherwise. Duplicated rather than fetched because the guest page
 * is handed a title and nothing else, and a header lettering the same meeting
 * differently from the host's is exactly the drift this pairing avoids.
 * Returns '' for a nameless meeting, leaving the element's own fallback.
 * @param {?string} title
 * @returns {string}
 */
function chatMeetTitleInitials(title) {
  const name = (title || '').trim();
  if (name === '') return '';
  const parts = name.includes(',') ? name.split(',') : name.split(/\s+/);
  return parts
    .slice(0, 2)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');
}

const CHAT_MEET_BACKOFF_START_MS = 1000;
const CHAT_MEET_BACKOFF_MAX_MS = 30000;
// Tailwind's md, which is where the aside stops being a slide-over.
const CHAT_MEET_PANE_ON_SCREEN = '(min-width: 768px)';

window.chatMeetSseMixin = function chatMeetSseMixin() {
  return {
    _streamAbort: null,
    _lastEventId: null,
    _streamBackoffMs: CHAT_MEET_BACKOFF_START_MS,
    _retryTimer: null,

    _openStream() {
      if (this._streamAbort) return Promise.resolve();
      const controller = new AbortController();
      this._streamAbort = controller;
      const headers = { 'X-Meeting-Token': this.token };
      if (this._lastEventId) headers['Last-Event-ID'] = this._lastEventId;
      // Per connection, not per page, and three different things: whether
      // the server answered at all, and whether it then sent any bytes. A
      // 600s budget spent on keepalive comments alone carries no frame but is
      // a perfectly healthy connection, so "no frame" cannot be the test.
      let answered = false;
      let receivedBytes = false;
      return fetch(`/api/v1/chat/meet/${this.slug}/stream`, { headers, signal: controller.signal })
        .then(async (resp) => {
          if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`);
          answered = true;
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value && value.length) {
              receivedBytes = true;
              // Bytes are what prove the connection healthy, not the status
              // line: a stream that 200s and dies every time would otherwise
              // reconnect forever at the shortest delay.
              this._streamBackoffMs = CHAT_MEET_BACKOFF_START_MS;
            }
            buffer += decoder.decode(value, { stream: true });
            const { frames, rest } = chatMeetParseSseChunk(buffer);
            buffer = rest;
            for (const frame of frames) {
              if (frame.id) this._lastEventId = frame.id;
              await this._dispatchStreamEvent(frame.payload);
            }
          }
        })
        .catch(() => { /* transport failure or non-2xx: answered stays false */ })
        .finally(() => {
          this._streamAbort = null;
          if (controller.signal.aborted) return;
          if (this.phase !== 'lobby' && this.phase !== 'room') return;
          // A 2xx that closed without a single byte is the server saying
          // "nothing for you": the guest was refused, removed or the meeting
          // ended before this connection. Ask /state once instead of
          // reconnecting into the same empty answer forever.
          if (answered && !receivedBytes) return this.resume();
          this._scheduleRetry(() => this._openStream());
        });
    },

    _closeStream() {
      if (this._streamAbort) {
        this._streamAbort.abort();
        this._streamAbort = null;
      }
    },

    // One ladder for both the stream and the state read: whichever of the two
    // is failing, the page keeps exactly one timer pending and backs off. One
    // handle is what makes that true - a state read failing while a stream
    // retry is already pending would otherwise leave two timers climbing the
    // same ladder, at half the delay each rung claims.
    _scheduleRetry(fn) {
      if (this.phase !== 'lobby' && this.phase !== 'room') return;
      this._cancelRetry();
      this._retryTimer = setTimeout(fn, this._streamBackoffMs);
      this._streamBackoffMs = Math.min(this._streamBackoffMs * 2, CHAT_MEET_BACKOFF_MAX_MS);
    },

    _cancelRetry() {
      if (this._retryTimer !== null) {
        clearTimeout(this._retryTimer);
        this._retryTimer = null;
      }
    },

    async _dispatchStreamEvent(payload) {
      const name = payload.event || '';
      if (name.startsWith('call_')) {
        window.dispatchEvent(new CustomEvent('chat-' + name, { detail: payload.data }));
      } else if (name === 'message') {
        await this.onMeetingMessage(payload.data && payload.data.message);
      } else if (name.startsWith('meeting_')) {
        await this.onMeetingEvent(payload);
      }
    },
  };
};

window.chatMeetMessagesMixin = function chatMeetMessagesMixin() {
  return {
    // Below md the chat is a slide-over panel, so a message can arrive with
    // nobody looking at it. The count is only ever shown there: at md and
    // above the panel is always on screen and the badge is not rendered.
    chatOpen: false,
    unreadMessages: 0,
    _paneVisibilityQuery: null,
    _onPaneVisibility: null,

    // At md and above the aside is always on screen (hidden md:flex) and
    // nothing ever sets chatOpen there, so "was it opened" cannot stand for
    // "was it seen": counting on it alone means narrowing the window later
    // raises a badge for messages the guest had been staring at.
    _paneHidden() {
      return !this.chatOpen && !window.matchMedia(CHAT_MEET_PANE_ON_SCREEN).matches;
    },

    // Widening puts the panel back on screen with nobody having opened it,
    // which is the same "it is in front of you now" the toggle means.
    _watchPaneVisibility() {
      const query = window.matchMedia(CHAT_MEET_PANE_ON_SCREEN);
      this._onPaneVisibility = (event) => {
        if (event.matches) this.unreadMessages = 0;
      };
      this._paneVisibilityQuery = query;
      query.addEventListener('change', this._onPaneVisibility);
    },

    // Named, not destroy(): Alpine calls exactly one destroy() and object
    // spread hands it to whichever mixin declared one last, so the page's
    // teardowns are listed on the root literal instead (see below).
    releasePaneVisibility() {
      if (this._paneVisibilityQuery && this._onPaneVisibility) {
        this._paneVisibilityQuery.removeEventListener('change', this._onPaneVisibility);
        this._paneVisibilityQuery = null;
        this._onPaneVisibility = null;
      }
    },

    toggleChat() {
      this.chatOpen = !this.chatOpen;
      if (this.chatOpen) {
        this.unreadMessages = 0;
        this.$nextTick(() => this.scrollToBottom());
      }
    },

    // -- chatMessagesMixin transport seam --------------------
    // The pane is the member pane: the same mixin, the same server partial,
    // the same alpine-ajax merge pipeline. Only the addressing changes, and
    // it changes here. Every guest request carries the meeting token in a
    // header, so the same four hooks the member pane leaves at their
    // defaults are the whole of the difference.
    _messagesPartialUrl(conversationId, cursor) {
      const base = `/meet/${this.slug}/messages`;
      return cursor ? `${base}?before=${cursor}` : base;
    },
    _messagesPartialHeaders() { return this._callHeaders(); },
    // The partial above is a UI route; posting a message is the guest API,
    // reached through the same seam every other guest request uses. The two
    // are different endpoints on purpose: one renders HTML for a browser,
    // the other takes JSON and has no session to check a CSRF token against.
    _messageEndpoint() { return this._callEndpoint('messages'); },
    _messageHeaders(options) { return this._callHeaders(options); },
    // A guest's membership is the meeting, not a conversation row: there is
    // no read cursor of its own to move, and no typing channel to speak on.
    _canMarkRead() { return false; },
    _canSendTyping() { return false; },
    // A guest is never shown who else is in the conversation, so there is
    // nobody to mention - @everyone included.
    _mentionCandidates() { return null; },
    _viewerIsGuest() { return true; },
    _onSendFailed() { window.AppAlert.error('Your message was not sent.'); },
    // The server stamps the slug on every mergeable element it renders for
    // a guest: the conversation the meeting chat lives in is not something
    // the guest is ever told.
    _expectedListKey() { return this.slug; },

    // The author of the optimistic bubble. No user row, hence no id and no
    // avatar to fetch - a name and the Guest badge, which is exactly what
    // the server-rendered bubble that replaces it will carry.
    _getCurrentUser() {
      return { id: null, username: this.displayName, is_guest: true };
    },

    // -- Collaborators the member pane has and a guest has not ----
    // chatMessagesMixin calls these on the send path (the sidebar row it
    // bubbles to the top, the draft it clears, the AI badge in a header).
    // There is no sidebar, no draft store and no bot here.
    isBotConversation() { return false; },
    isBotMessage() { return false; },
    botTyping: false,
    recorderSupported: false,
    _clearDraft() {},
    _updateConversationLastMessage() {},
    refreshConversationItems() {},

    // A reply quote links to the message it quotes. The member pane can page
    // the flow back looking for one that has scrolled off; a guest's window
    // on the conversation starts at its admission, so the copy is either on
    // screen or it is not.
    scrollToMessage(uuid) {
      const el = document.getElementById(`msg-${uuid}`);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
      setTimeout(() => {
        el.classList.remove('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
      }, 2000);
    },

    // The pane lives inside <template x-if="phase === 'room'">, so it is not
    // in the document until Alpine has flushed the phase change. alpine-ajax
    // resolves a request's targets when the request is issued, so asking any
    // earlier merges into nothing at all - and nothing retries.
    async loadRoomMessages() {
      await this.$nextTick();
      return this.loadMessages();
    },

    // A message frame off the event stream. The list is server-rendered, so
    // the frame is a signal to re-render rather than a payload to append -
    // the member pane answers its own SSE the same way. The frame is still
    // read for one thing: whether it is news to somebody not looking.
    onMeetingMessage(message) {
      if (this._paneHidden() && !this.isOwnMessage(message)) this.unreadMessages += 1;
      return this._refreshCurrentMessages();
    },

    isOwnMessage(message) {
      const author = message && message.author;
      return !!(author && author.participant_key
        && author.participant_key === this.currentParticipantKey);
    },
  };
};

function chatMeetApp(slug) {
  // Held so destroy() below can reach each mixin's own teardown by name,
  // the way room.js does: spread copies values, so a destroy() declared in
  // any of them would otherwise be shadowed by the next one that has one.
  const uiHelpers = chatUiHelpersMixin();
  const messages = chatMessagesMixin();
  const input = chatInputMixin();
  const call = chatCallMixin();
  const sse = chatMeetSseMixin();
  const meetMessages = chatMeetMessagesMixin();

  return {
    // The pane's own mixins, spread before the meet ones so the transport
    // seams below win. None of the four registers anything at construction
    // or exposes a getter, which is what makes spreading them safe.
    ...uiHelpers,
    ...messages,
    ...input,
    ...call,
    ...sse,
    ...meetMessages,

    slug,
    phase: 'name',
    summary: null,
    displayName: '',
    token: null,
    knocking: false,
    error: '',
    joinError: '',
    overReason: null,
    callRole: 'room',
    // Two readers, one object. The call mixin gates several of its methods
    // on "is there a conversation to talk to", and the conversation pane
    // reads uuid, kind and members. A guest learns none of the three: the
    // slug stands in for the uuid because it is what addresses the guest
    // endpoints AND what the server stamps on the partial it renders, so
    // the stale-merge veto compares like for like. The roster stays empty -
    // that is the mention list, and a guest is shown no roster.
    activeConversation: { uuid: slug, kind: 'group', members: [] },
    // The guest page has no preferences endpoint and no dialog to change
    // them, so the pane gets the shipped defaults rather than chatPrefs
    // from chat_preferences.js.
    chatPrefs: { compactMessageView: false, messageAnimation: 'slide', showThreadRepliesInline: false },
    speakingIds: {},
    pinnedKey: null,
    pinnedManually: false,
    callElapsed: '00:00',
    _callStartMs: null,
    _durationTimer: null,
    _reapRejoins: 0,
    _joinRefusal: null,
    _refusedOnce: false,
    // After the spreads: chatCallMixin declares its own null default.
    currentParticipantKey: null,

    async init() {
      this._initCallSounds?.();
      this._watchPaneVisibility();
      await this.loadSummary();
      const stored = sessionStorage.getItem(`meet:${slug}`);
      let saved = null;
      try {
        saved = stored ? JSON.parse(stored) : null;
      } catch (e) {
        saved = null;
      }
      if (saved === null) {
        if (stored) this.reset();
      } else {
        this.token = saved.token;
        this.displayName = saved.displayName;
        this.currentParticipantKey = saved.participantKey;
        await this.resume();
      }
      // Whenever a token is held, not only mid-call: a guest who closes the
      // tab from the lobby or the waiting card is leaving just as much as one
      // who closes it from the stage.
      window.addEventListener('pagehide', () => this._leaveBeacon());
    },

    // Alpine calls exactly one destroy(), and object spread would let the
    // last mixin that declares one win in silence. Every teardown the page
    // owns is listed here, after the spreads, so a mixin growing its own
    // cannot quietly drop another's.
    destroy() {
      this.releasePaneVisibility();
      this._cancelMessagesRetry();
      for (const mixin of [uiHelpers, messages, input, call, sse, meetMessages]) {
        mixin.destroy?.call(this);
      }
    },

    // -- Transport seam --------------------------------------
    // Every chatCallMixin request goes to the meet endpoints with the token,
    // and the "state" read replaces the conversation's /call.
    _callEndpoint(action) {
      return `/api/v1/chat/meet/${this.slug}/${action || 'state'}`;
    },
    _callHeaders({ json = false } = {}) {
      const headers = { 'X-Meeting-Token': this.token || '' };
      if (json) headers['Content-Type'] = 'application/json';
      return headers;
    },
    // The slug is what addresses every guest endpoint, and holding a token is
    // what makes there be anything to say goodbye with - not being in a call.
    // The leave endpoint answers 200 for an admitted guest with no participant
    // row (leave_call_as_guest is a no-op then), so the waiting card and the
    // pagehide beacon can both go through this one seam. _callEndpoint
    // discards the value, so this is read for its truthiness as much as for
    // what it names.
    _leaveTarget() {
      return this.token ? this.slug : null;
    },
    // The member room re-advertises a joinable call in a banner; this page
    // has none, and leaveCall's tail call would otherwise re-read the state
    // of a call the guest has just left.
    _syncCallBanner() {},

    // A refused join is not the end of the meeting: the host locking the room
    // between the admission and the join is ordinary, and it must leave the
    // guest in the room with something to press. Recorded rather than acted
    // on, so joinWhenCallStarts owns every transition out of the attempt and
    // cannot overwrite this reason with its generic one.
    _onJoinRefused(status, detail) {
      if (status === 404) {
        // The token stopped naming an admitted guest of this occurrence: ask
        // for our own status rather than inventing one.
        this._joinRefusal = { resume: true };
        return;
      }
      if (status === 423) {
        this._joinRefusal = { reason: 'The host has locked the meeting.' };
        return;
      }
      this._joinRefusal = {
        reason: detail || 'You could not be connected to the call.',
      };
    },

    async _sendHeartbeat() {
      if (!this.inCall) return;
      let resp;
      try {
        resp = await fetch(this._callEndpoint('heartbeat'), {
          method: 'POST',
          headers: this._callHeaders({ json: true }),
          body: JSON.stringify({ media_state: this._mediaState() }),
        });
      } catch (e) { return; }
      // The call ended under us: nothing to be in any more, but the meeting
      // is still open and the host may start another one.
      if (resp.status === 409) {
        this.callSession = null;
        await this.leaveCall();
        await this.waitForCall();
        return;
      }
      // Reaped by the stale sweep (12s without a heartbeat), or the token
      // stopped resolving. Tear the call down before asking for our own state
      // again: the interval and the microphone capture from this round would
      // otherwise both survive into the next one.
      if (resp.status === 400 || resp.status === 404) {
        this.callSession = null;
        await this.leaveCall();
        // The re-join arms a heartbeat that fires at once, so a server that
        // refuses every heartbeat would re-capture the microphone in a tight
        // loop. Give up after a couple of tries and let the guest decide.
        if (this._reapRejoins >= 2) {
          await this.waitForCall('We lost your place in the call.');
          return;
        }
        this._reapRejoins += 1;
        await this.resume();
        return;
      }
      if (resp.ok) this._reapRejoins = 0;
    },

    async _refreshCallState() {
      const resp = await fetch(this._callEndpoint(''), { headers: this._callHeaders() });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.participant_key) this.currentParticipantKey = data.participant_key;
      if (data.ice_servers) this._iceServers = data.ice_servers;
      this.callSession = data.active ? data : null;
      this.callParticipants = data.active ? (data.participants || []) : [];
    },

    // -- The meeting itself ----------------------------------
    async loadSummary() {
      try {
        const resp = await fetch(`/api/v1/chat/meet/${this.slug}`);
        this.summary = resp.ok ? await resp.json() : null;
      } catch (e) { /* the summary is decoration: the state read is the gate */ }
    },
    summaryLine() {
      if (!this.summary || !this.summary.start) return '';
      const d = new Date(this.summary.start);
      return isNaN(d) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    },
    isFull() {
      return !!(this.summary && this.summary.max_participants
        && this.summary.participant_count >= this.summary.max_participants);
    },
    capacityLabel() {
      const max = this.callSession && this.callSession.max_participants;
      return max ? `${this.callParticipants.length} / ${max}` : String(this.callParticipants.length);
    },
    waitingCapacityLine() {
      if (!this.summary || !this.summary.max_participants) return '';
      return `${this.summary.participant_count} / ${this.summary.max_participants} in the call`;
    },

    async knock() {
      const name = this.displayName.trim();
      if (this.knocking || !name) return;
      this.knocking = true;
      this.error = '';
      try {
        const resp = await fetch(`/api/v1/chat/meet/${this.slug}/knock`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ display_name: name }),
        });
        if (resp.status === 423) { this.error = 'This meeting is locked.'; return; }
        if (resp.status === 429) { this.error = 'Too many attempts. Please wait a moment.'; return; }
        // The server strips control characters before validating, so a name
        // made only of those comes back rejected even though it looked typed.
        if (resp.status === 400) { this.error = 'Please enter a name.'; return; }
        if (!resp.ok) { this.error = 'This meeting cannot be joined right now.'; return; }
        const data = await resp.json();
        this.token = data.token;
        this.displayName = data.display_name || name;
        this.currentParticipantKey = data.participant_key;
        sessionStorage.setItem(`meet:${this.slug}`, JSON.stringify({
          token: this.token,
          displayName: this.displayName,
          participantKey: this.currentParticipantKey,
        }));
        this.phase = 'lobby';
        this._openStream();
        await this.loadSummary();
      } catch (e) {
        this.error = 'This meeting cannot be joined right now.';
      } finally {
        this.knocking = false;
      }
    },

    async resume() {
      // "over" is a one-way door, and this is one of the three ways back
      // through it: a retry scheduled before the guest left, firing after.
      if (this.phase === 'over') return;
      let resp;
      try {
        resp = await fetch(this._callEndpoint(''), { headers: this._callHeaders() });
      } catch (e) {
        this._scheduleRetry(() => this.resume());
        return;
      }
      // The token names nobody here: nothing to resume, back to the name form.
      if (resp.status === 404) { this.reset(); return; }
      // Throttled, or the server is having a moment. Retrying is what keeps a
      // lobby from sitting there with no stream and no timer pending.
      if (!resp.ok) { this._scheduleRetry(() => this.resume()); return; }
      const data = await resp.json();
      if (data.admitted) {
        this.phase = 'room';
        this._openStream();
        await this.loadRoomMessages();
        await this.joinWhenCallStarts();
      } else if (data.state === 'waiting') {
        this.phase = 'lobby';
        this._openStream();
        // Not the value the page opened with: a lock or a full call can have
        // happened while this guest was away, and isFull() is read here.
        await this.loadSummary();
      } else {
        this.finish(data.state === 'refused' || data.state === 'removed' ? data.state : 'ended');
      }
    },

    // Joining is attempted exactly twice: when the room opens, and when
    // call_started says there is now something to join. Nothing polls.
    async joinWhenCallStarts() {
      if (this.phase === 'over' || this.inCall || this.joiningCall) return;
      this.joinError = '';
      await this._refreshCallState();
      if (!this.callSession) {
        await this.loadSummary();
        return;
      }
      this._joinRefusal = null;
      await this.startOrJoinCall();
      if (this._joinRefusal) {
        const refusal = this._joinRefusal;
        this._joinRefusal = null;
        if (refusal.resume && !this._refusedOnce) {
          // Once, not in a loop: a server whose state read keeps saying
          // "admitted" while its join keeps answering 404 would otherwise be
          // asked forever, a microphone capture per round.
          this._refusedOnce = true;
          await this.resume();
          return;
        }
        await this.waitForCall(refusal.reason || 'You could not be connected to the call.');
        return;
      }
      if (this.inCall) {
        this._refusedOnce = false;
        // Deliberately not resetting _reapRejoins here: a join the server
        // accepts proves nothing about presence, and resetting on it is what
        // would turn "reaped, re-join, reaped again" into an endless loop.
        // Only a heartbeat the server accepts clears the count.
        this._startDurationTimer();
        return;
      }
      // startOrJoinCall reports the specific cause as a toast (a denied
      // microphone, a full call) and returns; the room says so too, so the
      // guest is not left staring at an empty stage.
      await this.waitForCall('You were not connected. Check that your microphone is allowed, then try again.');
    },

    // In the room, with no call to be in. Not a terminal state: the stream
    // stays open, and call_started is what ends the wait.
    async waitForCall(reason = '') {
      if (this.phase === 'over') return;
      // Every failed attempt ends here, and it is the last thing an attempt
      // does - joinWhenCallStarts returns straight after - so clearing the
      // one-retry budget cannot reopen the loop within this attempt, and the
      // next one gets its retry back whichever way it starts: call_started,
      // Try again, or a fresh admission. Clearing on call_started alone would
      // leave the button without one.
      this._refusedOnce = false;
      this.joinError = reason;
      this.phase = 'room';
      this._stopDurationTimer();
      this.callElapsed = '00:00';
      await this.loadSummary();
    },

    async onMeetingEvent(payload) {
      switch (payload.event) {
        case 'meeting_admitted':
          this.phase = 'room';
          await this.loadRoomMessages();
          await this.joinWhenCallStarts();
          break;
        case 'meeting_refused': this.finish('refused'); break;
        case 'meeting_removed': await this.leaveCall(); this.finish('removed'); break;
        case 'meeting_ended': await this.leaveCall(); this.finish('ended'); break;
        default: break;
      }
    },

    // The base compares detail.conversation_id against activeConversation.uuid;
    // a guest's copy of call_started carries no conversation id at all, so the
    // base would drop the one event a waiting guest is here for.
    onCallStarted() {
      return this.joinWhenCallStarts();
    },

    async onCallEnded(detail) {
      if (this.callSession && detail && detail.session_id !== this.callSession.session_id) return;
      // Cleared first, so leaveCall tears the local call down without posting
      // a leave for a session the server has already closed.
      this.callSession = null;
      this.callParticipants = [];
      await this.leaveCall();
      await this.waitForCall();
    },

    // Copied from the member room: a departing peer that held the manual pin
    // releases it, so the stage falls back to the automatic spotlight.
    onCallParticipantLeft(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      if (detail.participant_key !== this.currentParticipantKey) this._playCallCue('peer-leave');
      this.callParticipants = this.callParticipants.filter((p) => p.participant_key !== detail.participant_key);
      this._closePeer(detail.participant_key);
      if (this.pinnedKey === detail.participant_key) {
        this.pinnedKey = null;
        this.pinnedManually = false;
      }
    },

    leaveLobby() {
      this._closeStream();
      this.reset();
    },

    leaveRoom() {
      return this.leaveCall().finally(() => this.finish('left'));
    },

    finish(reason) {
      this._closeStream();
      this._cancelRetry();
      this._stopDurationTimer();
      // The token dies with the page. Without this, a reload would restore it,
      // /state would still answer "admitted" (leaving only stamps left_at) and
      // the guest would be put back in the call, microphone live, having
      // touched nothing.
      this._forgetToken();
      this.overReason = reason;
      this.phase = 'over';
    },

    overTitle() {
      return {
        refused: 'The host did not let you in',
        removed: 'You were removed from the meeting',
        ended: 'The meeting has ended',
        left: 'You left the meeting',
      }[this.overReason] || 'Meeting closed';
    },
    overDetail() {
      return (this.overReason === 'refused' || this.overReason === 'removed')
        ? 'You can close this tab.'
        : 'Thanks for joining.';
    },
    canReturn() {
      return this.overReason !== 'refused' && this.overReason !== 'removed';
    },

    _forgetToken() {
      sessionStorage.removeItem(`meet:${this.slug}`);
      this.token = null;
      this.currentParticipantKey = null;
    },

    reset() {
      this._closeStream();
      this._cancelRetry();
      this._stopDurationTimer();
      this._forgetToken();
      this.overReason = null;
      this.error = '';
      this.joinError = '';
      this.phase = 'name';
    },

    // -- Call duration ---------------------------------------
    _startDurationTimer() {
      if (this._durationTimer) return;
      const serverTs = this.callSession && this.callSession.started_at;
      const start = serverTs ? new Date(serverTs).getTime() : Date.now();
      this._callStartMs = isNaN(start) ? Date.now() : start;
      this.callElapsed = window.chatRoomFormatDuration(Date.now() - this._callStartMs);
      this._durationTimer = setInterval(() => {
        this.callElapsed = window.chatRoomFormatDuration(Date.now() - this._callStartMs);
      }, 1000);
    },
    _stopDurationTimer() {
      if (this._durationTimer) { clearInterval(this._durationTimer); this._durationTimer = null; }
    },

    // -- Stage helpers the shared partial reads --------------
    // Same names, same bodies as the member room's (room.js), so both pages
    // compute their tiles through the same call_room.js helpers.
    isSpeaking(participantKey) {
      return !!this.speakingIds[participantKey];
    },
    remoteParticipants() {
      return this.callParticipants.filter((p) => p.participant_key !== this.currentParticipantKey);
    },
    selfParticipant() {
      return this.callParticipants.find((p) => p.participant_key === this.currentParticipantKey) || null;
    },
    gridColumns() {
      return Math.max(1, Math.ceil(Math.sqrt(this.remoteParticipants().length || 1)));
    },
    pinTile(participantKey) {
      this.pinnedKey = (this.pinnedKey === participantKey) ? null : participantKey;
      this.pinnedManually = true;
    },
    backToGrid() {
      this.pinnedKey = null;
      this.pinnedManually = true;
    },
    spotlightKey() {
      return window.chatCallSpotlightTarget(this.callParticipants, this.pinnedKey, this.pinnedManually);
    },
    isSpotlight() {
      return this.spotlightKey() != null;
    },
    spotlightParticipant() {
      const key = this.spotlightKey();
      return key == null ? null : this.callParticipants.find((p) => p.participant_key === key) || null;
    },
    stripParticipants() {
      const key = this.spotlightKey();
      return this.callParticipants.filter((p) => p.participant_key !== key);
    },
    hasVideo(p) {
      if (p && p.participant_key === this.currentParticipantKey) return !!(this.cameraOn || this.sharing);
      return !!(p && p.media_state && (p.media_state.video || p.media_state.screen));
    },
    streamFor(participantKey) {
      if (participantKey === this.currentParticipantKey) return this.localVideoStream || null;
      return this.remoteStreams[participantKey] || null;
    },
  };
}

window.chatMeetParseSseChunk = chatMeetParseSseChunk;
window.chatMeetTitleInitials = chatMeetTitleInitials;
window.chatMeetApp = chatMeetApp;
