'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_body_frame.js');
const { mailFrameCsp, mailFrameDocument } = ctx;

function cspOf(doc) {
  return /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(doc)[1];
}

test('the frame blocks remote images and every other fetch by default', () => {
  const csp = mailFrameCsp(false);
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /img-src data:(;|$)/);
  assert.doesNotMatch(csp, /https:/);
});

test('allowing images opens img-src to the network and nothing else', () => {
  const csp = mailFrameCsp(true);
  assert.match(csp, /img-src data: https: http:/);
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /form-action 'none'/);
});

test('the policy is in the head, before the body it governs', () => {
  const doc = mailFrameDocument('<p>Hello</p>', { allowImages: false });
  const head = doc.slice(0, doc.indexOf('<body>'));
  assert.equal(cspOf(head), mailFrameCsp(false));
  assert.ok(doc.endsWith('<body><p>Hello</p></body></html>'));
});

test('links default to a new tab', () => {
  assert.match(mailFrameDocument(''), /<base target="_blank">/);
});

test('theme colors and color scheme reach the frame stylesheet', () => {
  const doc = mailFrameDocument('', {
    theme: { color: 'rgb(1, 2, 3)', link: 'oklch(60% 0.2 250)', colorScheme: 'dark', fontFamily: 'Inter' },
  });
  assert.match(doc, /color-scheme: dark; color: rgb\(1, 2, 3\); font-family: Inter;/);
  assert.match(doc, /a \{ color: oklch\(60% 0\.2 250\);/);
});

test('a theme value cannot close the style element or add a rule', () => {
  const doc = mailFrameDocument('', {
    theme: { color: 'red</style><img src=x>', fontFamily: 'x; } body { display: none' },
  });
  assert.equal(doc.match(/<\/style>/g).length, 1);
  assert.doesNotMatch(doc, /<img/);
  // What is left stays one invalid value inside the :root rule.
  const root = doc.split('\n').find((line) => line.startsWith(':root'));
  assert.equal(root.match(/[{}]/g).join(''), '{}');
});
