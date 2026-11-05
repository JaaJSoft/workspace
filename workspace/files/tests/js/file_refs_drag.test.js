// Files dragged as references (file_refs_drag.js): the payload a Files or
// Notes row writes at dragstart and the markdown editor reads at drop.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const ctx = loadScripts([
  'workspace/common/static/ui/js/uuid.js',
  'workspace/files/ui/static/files/ui/js/file_refs_drag.js',
]);
const drag = ctx.fileRefsDrag;
const UUID = '01929b6e-5c3a-7d4e-8f00-0123456789ab';

function dataTransfer() {
  const data = new Map();
  return {
    get types() { return Array.from(data.keys()); },
    setData(type, value) { data.set(type, value); },
    getData(type) { return data.has(type) ? data.get(type) : ''; },
  };
}

test('what a row writes is what the editor reads', () => {
  const dt = dataTransfer();
  assert.equal(drag.write(dt, [{ uuid: UUID, name: 'report.pdf', extra: 1 }]), true);
  assert.equal(drag.has(dt), true);
  assert.deepEqual(Array.from(drag.read(dt), (ref) => ({ ...ref })), [{ uuid: UUID, name: 'report.pdf' }]);
});

test('nothing to write leaves the drag without a payload', () => {
  const dt = dataTransfer();
  assert.equal(drag.write(dt, []), false);
  assert.equal(drag.has(dt), false);
  assert.equal(drag.read(dt).length, 0);
});

test('a foreign drag carries no references', () => {
  const dt = dataTransfer();
  dt.setData('text/plain', UUID);
  assert.equal(drag.has(dt), false);
  assert.equal(drag.read(dt).length, 0);
  assert.equal(drag.has(null), false);
});

test('a forged payload yields only well-formed references', () => {
  const dt = dataTransfer();
  dt.setData(drag.MIME, JSON.stringify([
    { uuid: '../../etc/passwd', name: 'x' },
    { uuid: UUID.toUpperCase(), name: 42 },
    'junk',
  ]));
  assert.deepEqual(Array.from(drag.read(dt), (ref) => ({ ...ref })), [{ uuid: UUID, name: '' }]);
});

test('unparsable JSON is no references', () => {
  const dt = dataTransfer();
  dt.setData(drag.MIME, '{not json');
  assert.equal(drag.read(dt).length, 0);
});
