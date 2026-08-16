'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadScripts } = require('../../../common/tests/js/loader');

// The bubble markup comes from the real partial, so the test exercises the
// exact templates the browser clones from.
const OPTIMISTIC_PARTIAL = fs.readFileSync(
  path.resolve(
    __dirname,
    '../../ui/templates/chat/ui/partials/_optimistic_message.html',
  ),
  'utf8',
);

function parseTemplates(partialHtml) {
  const templates = new Map();
  for (const m of partialHtml.matchAll(/<template id="([^"]+)">([\s\S]*?)<\/template>/g)) {
    templates.set(m[1], { innerHTML: m[2] });
  }
  return templates;
}

/**
 * Pin the optimistic-message lifecycle: sending injects a pending bubble
 * into the messages container immediately, and the bubble is removed once
 * the real server-rendered message replaces it (or the send fails).
 *
 * The DOM stub captures the injected HTML as a string, so the assertions
 * check the observable markup (ids, classes, escaped content) rather than
 * a live tree.
 */
function buildDom() {
  const inserted = [];
  const injectedById = new Map();
  const container = {
    insertAdjacentHTML(position, html) {
      assert.equal(position, 'beforeend');
      inserted.push(html);
      const m = html.match(/id="([^"]+)"/);
      if (m) {
        const id = m[1];
        injectedById.set(id, {
          html,
          remove() { injectedById.delete(id); },
        });
      }
    },
  };
  const templates = parseTemplates(OPTIMISTIC_PARTIAL);
  const document = {
    getElementById(id) {
      if (id === 'messages-container') return container;
      if (templates.has(id)) return templates.get(id);
      return injectedById.get(id) || null;
    },
  };
  return { document, inserted, injectedById };
}

function buildApp() {
  const dom = buildDom();
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/html.js',
      'workspace/common/static/ui/js/filesize.js',
      'workspace/chat/ui/static/chat/ui/js/messages.js',
    ],
    {
      document: dom.document,
      userAvatarTag: (userId, username) => `<user-avatar username="${username}"></user-avatar>`,
    },
  );
  const app = ctx.chatMessagesMixin();
  app.activeConversation = {
    uuid: 'c1',
    members: [{ user: { id: 7, username: 'alice' } }],
  };
  app.currentUserId = 7;
  return { app, ctx, ...dom };
}

test('a pending bubble is injected as an own-message group with a spinner', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_1', 'hello there', null, null);

  assert.equal(inserted.length, 1);
  const html = inserted[0];
  assert.match(html, /id="_optimistic_1"/);
  // Rendered as an own-message group, aligned right like the server partial
  assert.match(html, /msg-group-end/);
  assert.match(html, /flex-row-reverse/);
  assert.match(html, /msg-bubble/);
  // The sender's avatar rides along
  assert.match(html, /<user-avatar username="alice">/);
  // Pending state: a loading spinner where the timestamp normally sits
  assert.match(html, /loading-dots/);
  assert.match(html, /hello there/);
});

test('the body is HTML-escaped and line breaks become <br>', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_2', '<b>bold</b>\nline2', null, null);

  const html = inserted[0];
  assert.ok(!html.includes('<b>bold</b>'));
  assert.match(html, /&lt;b&gt;bold&lt;\/b&gt;<br>line2/);
});

test('a body-less message renders no message body block', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_3', '', null, [
    { name: 'doc.pdf', type: 'application/pdf', size: 2048 },
  ]);

  assert.ok(!inserted[0].includes('msg-body'));
});

test('the reply branch renders the quoted author and preview, escaped', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage(
    '_optimistic_4',
    'my answer',
    { uuid: 'm9', author: 'Bob <script>', body: 'original & text' },
    null,
  );

  const html = inserted[0];
  assert.match(html, /Bob &lt;script&gt;/);
  assert.match(html, /original &amp; text/);
  // The accent bar that visually marks a quote in the real bubble
  assert.match(html, /bg-info/);
});

test('image and video attachments render previews, other files a name + size chip', () => {
  const { app, ctx, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_5', 'caption', null, [
    { name: 'photo.png', type: 'image/png', _preview: 'blob:img-1' },
    { name: 'clip.mp4', type: 'video/mp4', _preview: 'blob:vid-1' },
    { name: 'doc "final".pdf', type: 'application/pdf', size: 123456 },
  ]);

  const html = inserted[0];
  // Image preview from the local object URL
  assert.match(html, /<img src="blob:img-1"/);
  // Video preview with the play overlay
  assert.match(html, /<video src="blob:vid-1"/);
  assert.match(html, /data-lucide="play"/);
  // Generic file: escaped name + human-readable size
  assert.match(html, /doc &quot;final&quot;\.pdf/);
  assert.ok(html.includes(ctx.formatFileSize(123456)));
  // With a body present, a separator sits between body and attachments
  assert.match(html, /border-t/);
});

test('an image without a local preview falls back to the generic file row', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_6', '', null, [
    { name: 'photo.png', type: 'image/png', size: 512 },
  ]);

  const html = inserted[0];
  assert.ok(!html.includes('<img'));
  assert.match(html, /photo\.png/);
});

test('the bubble is removed once the real message arrives', () => {
  const { app, injectedById } = buildApp();
  app._injectOptimisticMessage('_optimistic_7', 'bye', null, null);
  assert.ok(injectedById.has('_optimistic_7'));

  app._removeOptimisticMessage('_optimistic_7');
  assert.ok(!injectedById.has('_optimistic_7'));

  // Removing an id that is no longer there is a no-op, not an error
  app._removeOptimisticMessage('_optimistic_7');
});
