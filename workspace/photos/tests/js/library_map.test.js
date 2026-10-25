const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/photos/ui/static/photos/ui/js/library_map.js';

const utils = () => loadScript(SCRIPT).libraryMapUtils;

// [uuid, latitude, longitude, month, is_video, sensitive_level, thumbnail]
const point = (month, thumbnail = '') => ['0b5f', 48.85, 2.29, month, 0, 0, thumbnail];

test('months round-trip through their YYYY-MM token', () => {
  const { monthToken, parseMonth } = utils();
  assert.equal(monthToken(2024 * 12 + 6), '2024-07');
  assert.equal(parseMonth('2024-07'), 2024 * 12 + 6);
  assert.equal(parseMonth('2024-01') + 11, parseMonth('2024-12'));
  for (const bad of ['', null, '2024', '2024-13', '2024-00', '24-07']) {
    assert.equal(parseMonth(bad), null, String(bad));
  }
});

test('a month reads as its short name and year, whatever the local timezone', () => {
  assert.equal(utils().monthLabel(2024 * 12), 'Jan 2024');
  assert.equal(utils().monthLabel(2023 * 12 + 11), 'Dec 2023');
});

test('the box sent to the server wraps longitudes the map counted past 180', () => {
  const { bbox } = utils();
  assert.deepEqual(Array.from(bbox(2.2, 48.8, 2.4, 48.9)), [2.2, 48.8, 2.4, 48.9]);
  assert.deepEqual(Array.from(bbox(170, -10, 190, 10)), [170, -10, -170, 10]);
  assert.deepEqual(Array.from(bbox(-200, -10, -170, 10)), [160, -10, -170, 10]);
  // A world or more: every longitude, and latitudes clamped to the poles.
  assert.deepEqual(Array.from(bbox(-400, -95, 400, 95)), [-180, -90, 180, 90]);
});

test('the histogram spans the dated months, quiet ones included', () => {
  const { histogram } = utils();
  const result = histogram([point(100), point(null), point(103), point(100)]);
  assert.equal(result.first, 100);
  assert.equal(result.last, 103);
  assert.deepEqual(Array.from(result.counts), [2, 0, 0, 1]);
  assert.equal(histogram([point(null)]), null);
  assert.equal(histogram([]), null);
});

test('a group counts in thousands past a thousand', () => {
  const { countLabel } = utils();
  assert.equal(countLabel(7), '7');
  assert.equal(countLabel(999), '999');
  assert.equal(countLabel(1000), '1k');
  assert.equal(countLabel(1250), '1.3k');
  assert.equal(countLabel(12400), '12k');
});

test('a thumbnail URL carries the edit version, and none without a thumbnail', () => {
  const { thumbnailUrl } = utils();
  assert.equal(thumbnailUrl(point(1, '')), '/api/v1/files/0b5f/thumbnail?size=128');
  assert.equal(thumbnailUrl(point(1, 'r90 c1')), '/api/v1/files/0b5f/thumbnail?size=128&v=r90%20c1');
  assert.equal(thumbnailUrl(point(1, null)), '');
});

test('a marker past the antimeridian shows in the world copy in view', () => {
  const { nearestCopy } = utils();
  assert.equal(nearestCopy(-170, 175), 190);
  assert.equal(nearestCopy(170, -175), -190);
  assert.equal(nearestCopy(2.29, 10), 2.29);
});
