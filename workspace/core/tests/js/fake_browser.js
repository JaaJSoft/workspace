'use strict';

// A browser as sse.js sees it, with several tabs open at once. The tabs share
// what a real browser shares between pages of one origin - the lock manager
// and the broadcast channels - and each has its own page, EventSource and
// timers.
//
// Nothing here runs on its own clock: lock callbacks and channel messages are
// microtasks (await settle() to let them land), timers fire when a test
// advances them, and a suspended tab keeps what it was sent until it wakes.

const { loadScript } = require('../../../common/tests/js/loader');

const SSE_SCRIPT = 'workspace/core/static/core/js/sse.js';

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

function abortError() {
  return new DOMException('The request was aborted', 'AbortError');
}

function fakeTimers() {
  const timers = new Map();
  let now = 0;
  let lastId = 0;

  function schedule(callback, delay, every) {
    lastId += 1;
    timers.set(lastId, { at: now + delay, callback, every });
    return lastId;
  }

  return {
    setTimeout: (callback, delay) => schedule(callback, delay, null),
    setInterval: (callback, delay) => schedule(callback, delay, delay),
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let dueId = null;
        for (const [id, timer] of timers) {
          if (timer.at <= end && (dueId === null || timer.at < timers.get(dueId).at)) dueId = id;
        }
        if (dueId === null) break;
        const timer = timers.get(dueId);
        now = timer.at;
        if (timer.every) timer.at += timer.every;
        else timers.delete(dueId);
        timer.callback();
      }
      now = end;
    },
  };
}

class FakeBrowser {
  constructor() {
    this.held = new Map();
    this.queues = new Map();
    this.channels = [];
  }

  // -- what reaches a tab ---------------------------------------------------

  _run(tab, task) {
    queueMicrotask(() => {
      if (tab.closed) return;
      if (tab.suspended) tab.backlog.push(task);
      else task();
    });
  }

  /** The tab stops running, without a word: nothing it is sent reaches it. */
  suspend(tab) {
    tab.suspended = true;
  }

  wake(tab) {
    tab.suspended = false;
    const backlog = tab.backlog;
    tab.backlog = [];
    for (const task of backlog) task();
  }

  /** The tab is gone, without having run any handler. */
  close(tab) {
    tab.closed = true;
    for (const channel of this.channels) {
      if (channel.tab === tab) channel.closed = true;
    }
    for (const [name, queue] of this.queues) {
      this.queues.set(name, queue.filter((request) => request.tab !== tab));
    }
    for (const [name, request] of this.held) {
      if (request.tab !== tab) continue;
      this.held.delete(name);
      this._grantNext(name);
    }
  }

  // -- Web Locks ------------------------------------------------------------

  _queue(name) {
    if (!this.queues.has(name)) this.queues.set(name, []);
    return this.queues.get(name);
  }

  queued(name) {
    return this._queue(name).map((request) => request.tab);
  }

  holder(name) {
    const request = this.held.get(name);
    return request ? request.tab : null;
  }

  _grant(request) {
    this.held.set(request.name, request);
    this._run(request.tab, () => {
      Promise.resolve()
        .then(() => request.callback({ name: request.name }))
        .then(
          (value) => this._settle(request, () => request.resolve(value)),
          (error) => this._settle(request, () => request.reject(error)),
        );
    });
  }

  _settle(request, finish) {
    // A stolen lock already rejected its request.
    if (request.broken) return;
    if (this.held.get(request.name) === request) {
      this.held.delete(request.name);
      this._grantNext(request.name);
    }
    finish();
  }

  _grantNext(name) {
    const queue = this._queue(name);
    if (!this.held.has(name) && queue.length) this._grant(queue.shift());
  }

  requestLock(tab, name, options, callback) {
    return new Promise((resolve, reject) => {
      const request = { tab, name, callback, resolve, reject, broken: false };
      if (options.steal) {
        const holder = this.held.get(name);
        if (holder) {
          holder.broken = true;
          this._run(holder.tab, () => holder.reject(abortError()));
        }
        this._grant(request);
        return;
      }
      if (options.ifAvailable) {
        if (this.held.has(name) || this._queue(name).length) {
          this._run(tab, () => Promise.resolve(callback(null)).then(resolve, reject));
        } else {
          this._grant(request);
        }
        return;
      }
      if (options.signal) {
        options.signal.addEventListener('abort', () => {
          const queue = this._queue(name);
          const index = queue.indexOf(request);
          if (index === -1) return;
          queue.splice(index, 1);
          reject(abortError());
        });
      }
      this._queue(name).push(request);
      this._grantNext(name);
    });
  }

  // -- BroadcastChannel -----------------------------------------------------

  post(sender, data) {
    // A structured clone, as far as plain data goes.
    const text = JSON.stringify(data);
    for (const channel of [...this.channels]) {
      if (channel === sender || channel.name !== sender.name) continue;
      this._run(channel.tab, () => {
        if (!channel.closed && channel.onmessage) channel.onmessage({ data: JSON.parse(text) });
      });
    }
  }

  // -- tabs -----------------------------------------------------------------

  /**
   * Load sse.js in a new tab.
   *
   * @param {object} options
   * @param {string|null} options.userId - <body data-user-id>, null for none
   * @param {boolean} options.hidden - whether the tab opens in the background
   * @param {object|null} options.locks - navigator.locks; null for a browser
   *   without Web Locks, omitted for this browser's
   * @param {Array|Function} options.snapshot - the events the snapshot
   *   endpoint answers with, or a function returning the fetch promise
   */
  openTab({ userId = '7', hidden = false, locks, snapshot = [] } = {}) {
    const browser = this;
    const tab = {
      closed: false,
      suspended: false,
      backlog: [],
      events: [],
      sources: [],
      fetches: [],
      windowHandlers: {},
      documentHandlers: {},
      timers: fakeTimers(),
    };

    class TabEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;

      constructor(url) {
        this.url = url;
        this.readyState = TabEventSource.OPEN;
        this.listeners = {};
        tab.sources.push(this);
      }

      addEventListener(type, handler) {
        this.listeners[type] = handler;
      }

      close() {
        this.readyState = TabEventSource.CLOSED;
      }

      get closed() {
        return this.readyState === TabEventSource.CLOSED;
      }

      open() {
        this.onopen();
      }

      deliver(payload, lastEventId = '') {
        this.listeners.sse({ data: JSON.stringify(payload), lastEventId });
      }
    }

    class TabChannel {
      constructor(name) {
        this.name = name;
        this.tab = tab;
        this.closed = false;
        this.onmessage = null;
        browser.channels.push(this);
      }

      postMessage(data) {
        if (this.closed) throw new Error('postMessage on a closed channel');
        browser.post(this, data);
      }

      close() {
        this.closed = true;
      }
    }

    tab.document = {
      hidden,
      body: { dataset: userId === null ? {} : { userId } },
      getElementById: () => null,
      addEventListener: (type, handler) => {
        tab.documentHandlers[type] = handler;
      },
    };

    const tabLocks = locks === undefined
      ? { request: (name, options, callback) => browser.requestLock(tab, name, options, callback) }
      : locks;

    loadScript(SSE_SCRIPT, {
      EventSource: TabEventSource,
      BroadcastChannel: TabChannel,
      AbortController,
      CustomEvent: class {
        constructor(type, init) {
          this.type = type;
          this.detail = init && init.detail;
        }
      },
      document: tab.document,
      navigator: tabLocks ? { locks: tabLocks } : {},
      fetch: (url) => {
        tab.fetches.push(url);
        if (typeof snapshot === 'function') return snapshot(url);
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ events: snapshot }) });
      },
      addEventListener: (type, handler) => {
        tab.windowHandlers[type] = handler;
      },
      dispatchEvent: (event) => {
        // A detail parsed inside the vm carries that realm's prototypes, and
        // would never deep-equal a literal written in a test.
        const detail = event.detail === undefined
          ? undefined
          : JSON.parse(JSON.stringify(event.detail));
        tab.events.push({ type: event.type, detail });
      },
      setTimeout: tab.timers.setTimeout,
      clearTimeout: tab.timers.clearTimeout,
      setInterval: tab.timers.setInterval,
      clearInterval: tab.timers.clearInterval,
    });

    tab.types = () => tab.events.map((event) => event.type);
    tab.stream = () => tab.sources[tab.sources.length - 1];
    tab.openStreams = () => tab.sources.filter((source) => !source.closed);
    tab.show = () => {
      tab.document.hidden = false;
      tab.documentHandlers.visibilitychange();
    };
    tab.hide = () => {
      tab.document.hidden = true;
      tab.documentHandlers.visibilitychange();
    };
    return tab;
  }
}

module.exports = { FakeBrowser, settle };
