'use strict';

// A browser as sse.js sees it, with several tabs open at once. Each tab has
// its own page, EventSource and timers; they share the workers a real browser
// shares between the pages of one origin. A shared worker is sse.js loaded a
// second time, in a scope with no window and no document, as it is in
// production.
//
// Nothing here runs on its own clock: messages between a page and a worker
// are microtasks (await settle() to let them land) and timers fire when a
// test advances them.

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { loadScript } = require('../../../common/tests/js/loader');

const SSE_SCRIPT = 'workspace/core/static/core/js/sse.js';
const SCRIPT_URL = 'https://workspace.test/static/core/js/sse.js';
// workspace/core/tests/js -> repo root
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
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
    /** How many timers are still set. */
    pending: () => timers.size,
    /** Fires what falls due, one timer after the other, with no message delivered in between. */
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

/** An EventSource class whose instances a test drives by hand, kept in `owner.sources`. */
function eventSourceFor(owner) {
  return class FakeEventSource {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;

    constructor(url) {
      this.url = url;
      this.readyState = FakeEventSource.CONNECTING;
      this.listeners = {};
      // Each connection starts without one: the browser only repeats the last
      // id on its own reconnects.
      this.lastEventId = '';
      owner.sources.push(this);
    }

    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }

    close() {
      this.readyState = FakeEventSource.CLOSED;
    }

    get closed() {
      return this.readyState === FakeEventSource.CLOSED;
    }

    // A closed source fires nothing: a test driving one is mistaken.
    _alive() {
      if (this.closed) throw new Error(`${this.url} is closed`);
    }

    open() {
      this._alive();
      this.readyState = FakeEventSource.OPEN;
      this.onopen();
    }

    /** An event, with its id when it has one. Without, it carries the connection's last one. */
    deliver(payload, id) {
      this._alive();
      if (id) this.lastEventId = id;
      this.listeners.sse({ data: JSON.stringify(payload), lastEventId: this.lastEventId });
    }

    /** A connection lost: the browser reconnects on its own. */
    stumble() {
      this._alive();
      this.readyState = FakeEventSource.CONNECTING;
      this.onerror();
    }

    /** An answer the browser does not retry after: a 502, a 403. */
    fail() {
      this._alive();
      this.readyState = FakeEventSource.CLOSED;
      this.onerror();
    }
  };
}

/** One end of a MessageChannel. `isAlive` tells whether its side still runs. */
class FakePort {
  constructor(isAlive) {
    this.isAlive = isAlive;
    this.other = null;
    this.closed = false;
    this.waiting = [];
    this.handler = null;
  }

  // Assigning the handler starts the port: what was sent before is delivered.
  set onmessage(handler) {
    this.handler = handler;
    const waiting = this.waiting;
    this.waiting = [];
    for (const data of waiting) this.receive(data);
  }

  get onmessage() {
    return this.handler;
  }

  postMessage(data) {
    if (this.closed) return;
    // A structured clone, as far as plain data goes.
    const text = JSON.stringify(data);
    const target = this.other;
    queueMicrotask(() => target.receive(JSON.parse(text)));
  }

  receive(data) {
    if (this.closed || !this.isAlive()) return;
    if (!this.handler) this.waiting.push(data);
    else this.handler({ data });
  }

  close() {
    this.closed = true;
  }
}

class FakeBrowser {
  constructor() {
    this.workers = new Map();
    // A worker whose script fails never runs, and tells its pages nothing.
    this.workersStart = true;
    this.script = fs.readFileSync(path.join(REPO_ROOT, SSE_SCRIPT), 'utf8');
  }

  /** The only worker running. */
  worker() {
    const running = [...this.workers.values()];
    if (running.length !== 1) throw new Error(`${running.length} workers are running`);
    return running[0];
  }

  /** The worker ends without a word to its pages, as when the browser reclaims its memory. */
  kill(worker) {
    worker.dead = true;
    this.workers.delete(worker.key);
    for (const source of worker.sources) source.close();
  }

  /** The tab is gone, without having run any handler. A worker goes with its last page. */
  close(tab) {
    tab.closed = true;
    for (const worker of [...this.workers.values()]) {
      worker.tabs.delete(tab);
      if (!worker.tabs.size) this.kill(worker);
    }
  }

  _startWorker(key, name) {
    const worker = {
      key,
      name,
      dead: false,
      // While deaf, the worker misses what its pages send it.
      deaf: false,
      tabs: new Set(),
      sources: [],
      timers: fakeTimers(),
    };
    worker.stream = () => worker.sources[worker.sources.length - 1];
    // The worker's global: no window, no document, and `self` is the global.
    worker.scope = {
      SharedWorkerGlobalScope: class {},
      EventSource: eventSourceFor(worker),
      setTimeout: worker.timers.setTimeout,
      clearTimeout: worker.timers.clearTimeout,
    };
    worker.scope.self = worker.scope;
    if (this.workersStart) {
      vm.createContext(worker.scope);
      vm.runInContext(this.script, worker.scope, { filename: SSE_SCRIPT });
    }
    this.workers.set(key, worker);
    return worker;
  }

  _connect(tab, url, name) {
    const key = `${url} ${name}`;
    const worker = this.workers.get(key) || this._startWorker(key, name);
    worker.tabs.add(tab);
    const pagePort = new FakePort(() => !tab.closed);
    const workerPort = new FakePort(() => !worker.dead && !worker.deaf);
    pagePort.other = workerPort;
    workerPort.other = pagePort;
    queueMicrotask(() => {
      if (!worker.dead && worker.scope.onconnect) worker.scope.onconnect({ ports: [workerPort] });
    });
    return pagePort;
  }

  /**
   * Load sse.js in a new tab.
   *
   * @param {object} options
   * @param {string|null} options.userId - <body data-user-id>, null for none
   * @param {boolean} options.hidden - whether the tab opens in the background
   * @param {boolean|string} options.sharedWorker - false for a browser
   *   without them, 'refused' for one that throws when a page asks for one
   * @param {Array|Function} options.snapshot - the events the snapshot
   *   endpoint answers with, or a function returning the fetch promise
   * @param {boolean} options.currentScript - false when the page cannot tell
   *   which file it is running
   * @param {boolean} options.parsing - true while the document is still being
   *   parsed, as it is when a real page runs the script
   */
  openTab({
    userId = '7', hidden = false, sharedWorker = true, snapshot = [], currentScript = true,
    parsing = false,
  } = {}) {
    const browser = this;
    const tab = {
      closed: false,
      events: [],
      sources: [],
      fetches: [],
      // Every SharedWorker the page constructed, and whether the next one is refused.
      sharedWorkers: [],
      refusesSharedWorkers: sharedWorker === 'refused',
      windowHandlers: {},
      documentHandlers: {},
      timers: fakeTimers(),
    };

    class TabSharedWorker {
      constructor(url, options) {
        if (tab.refusesSharedWorkers) throw new DOMException('refused', 'SecurityError');
        this.url = url;
        this.name = options.name;
        this.onerror = null;
        this.port = browser._connect(tab, url, options.name);
        tab.sharedWorkers.push(this);
      }
    }

    tab.document = {
      hidden,
      readyState: parsing ? 'loading' : 'complete',
      currentScript: currentScript ? { src: SCRIPT_URL } : null,
      body: { dataset: userId === null ? {} : { userId } },
      getElementById: () => null,
      addEventListener: (type, handler) => {
        tab.documentHandlers[type] = handler;
      },
    };

    loadScript(SSE_SCRIPT, {
      EventSource: eventSourceFor(tab),
      SharedWorker: sharedWorker ? TabSharedWorker : undefined,
      AbortController,
      CustomEvent: class {
        constructor(type, init) {
          this.type = type;
          this.detail = init && init.detail;
        }
      },
      document: tab.document,
      fetch: (url, init) => {
        tab.fetches.push(url);
        if (typeof snapshot === 'function') return snapshot(url, init);
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
    tab.lastFetch = () => tab.fetches[tab.fetches.length - 1];
    tab.show = () => {
      tab.document.hidden = false;
      tab.documentHandlers.visibilitychange();
    };
    tab.hide = () => {
      tab.document.hidden = true;
      tab.documentHandlers.visibilitychange();
    };
    /** Set aside by the browser, then brought back: the back/forward cache. */
    tab.setAside = () => tab.windowHandlers.pagehide({ persisted: true });
    tab.bringBack = () => tab.windowHandlers.pageshow({ persisted: true });
    return tab;
  }
}

module.exports = { FakeBrowser, SCRIPT_URL, settle };
