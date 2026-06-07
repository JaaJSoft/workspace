'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// getCSRFToken reads document.cookie at call time, so we hand the script a
// mutable document stub and rewrite its cookie string before each assertion.
const document = { cookie: '' };
const ctx = loadScript('workspace/common/static/ui/js/csrf.js', { document });
const { getCSRFToken } = ctx;

test('extracts the token from a lone csrftoken cookie', () => {
  document.cookie = 'csrftoken=abc123';
  assert.equal(getCSRFToken(), 'abc123');
});

test('finds csrftoken among other cookies', () => {
  document.cookie = 'sessionid=xyz; csrftoken=tok456; theme=dark';
  assert.equal(getCSRFToken(), 'tok456');
});

test('stops the value at the next semicolon', () => {
  document.cookie = 'csrftoken=first;other=second';
  assert.equal(getCSRFToken(), 'first');
});

test('returns empty string when the cookie is absent', () => {
  document.cookie = 'sessionid=xyz; theme=dark';
  assert.equal(getCSRFToken(), '');
});

test('returns empty string for an empty cookie jar', () => {
  document.cookie = '';
  assert.equal(getCSRFToken(), '');
});

test('returns empty string for an empty csrftoken value', () => {
  // /csrftoken=([^;]+)/ requires at least one character after the "=".
  document.cookie = 'csrftoken=';
  assert.equal(getCSRFToken(), '');
});

test('does not match a cookie whose name merely ends with csrftoken', () => {
  // The name must match on a boundary (start of string or after "; "), so a
  // differently-named cookie like "mycsrftoken" cannot shadow the real token.
  document.cookie = 'mycsrftoken=wrong; csrftoken=right';
  assert.equal(getCSRFToken(), 'right');
});
