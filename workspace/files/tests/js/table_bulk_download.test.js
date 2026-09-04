'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

// The bulk bar's Download used to post the selection itself and drop every
// refusal on the floor. It now hands the selection to the browser component
// like every other bulk action, so one client owns the request and its
// error reporting.
test('the bulk bar download dispatches a bulk-action event with the selection', () => {
  const dispatched = [];
  const ctx = loadScripts([
    'workspace/files/ui/static/files/ui/js/file_actions.js',
    'workspace/files/ui/static/files/ui/js/table.js',
  ], {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }), getElementById: () => null },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    dispatchEvent: (event) => dispatched.push(event),
    fetch: () => assert.fail('the table must not post the download itself'),
  });
  const table = ctx.fileTableWithView();
  table.selectedUuids = new Set(['uuid-1', 'uuid-2']);

  table.executeBulkAction({ id: 'download' });

  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, 'bulk-action');
  assert.equal(dispatched[0].detail.action, 'download');
  assert.deepEqual(Array.from(dispatched[0].detail.uuids), ['uuid-1', 'uuid-2']);
});
