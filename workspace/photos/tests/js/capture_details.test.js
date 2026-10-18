const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/photos/ui/static/photos/ui/js/capture_details.js';

function load(extra = {}) {
  return loadScript(SCRIPT, extra);
}

const position = (ctx, text) => {
  const result = ctx.photosParseCoordinates(text);
  return result && { ...result };
};

test('reads coordinates as typed or copied from the properties panel', () => {
  const ctx = load();
  assert.deepEqual(position(ctx, '48.85837, 2.29448'), { latitude: 48.85837, longitude: 2.29448 });
  assert.deepEqual(position(ctx, ' -33.8568 151.2153 '), { latitude: -33.8568, longitude: 151.2153 });
  assert.deepEqual(position(ctx, '43.3;5.4'), { latitude: 43.3, longitude: 5.4 });
  assert.deepEqual(position(ctx, 'geo:43.3,5.4'), { latitude: 43.3, longitude: 5.4 });
});

test('reads the position out of a map link', () => {
  const ctx = load();
  assert.deepEqual(
    position(ctx, 'https://www.openstreetmap.org/?mlat=48.85837&mlon=2.29448#map=16/48.8/2.2'),
    { latitude: 48.85837, longitude: 2.29448 },
  );
  assert.deepEqual(
    position(ctx, 'https://www.openstreetmap.org/#map=17/43.29650/5.36980'),
    { latitude: 43.2965, longitude: 5.3698 },
  );
  assert.deepEqual(
    position(ctx, 'https://www.google.com/maps/place/Tour+Eiffel/@48.8583701,2.2919064,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x8ddca9ee380ef7e0!8m2!3d48.8583701!4d2.2944813'),
    { latitude: 48.8583701, longitude: 2.2944813 },
  );
  assert.deepEqual(
    position(ctx, 'https://www.google.com/maps/@-22.951,-43.2105,15z'),
    { latitude: -22.951, longitude: -43.2105 },
  );
  assert.deepEqual(
    position(ctx, 'https://maps.google.com/?q=40.6892,-74.0445'),
    { latitude: 40.6892, longitude: -74.0445 },
  );
});

test('refuses what is not a position on Earth', () => {
  const ctx = load();
  for (const text of ['', 'Paris', '91, 0', '0, 181', '48.8', 'https://example.com/']) {
    assert.equal(ctx.photosParseCoordinates(text), null, text);
  }
});

test('a shift counts days, hours and minutes, earlier when the sign says so', () => {
  const ctx = load();
  assert.equal(ctx.photosShiftMinutes(1, 0, 2, 0), 120);
  assert.equal(ctx.photosShiftMinutes(-1, 1, 1, 30), -1530);
  assert.equal(ctx.photosShiftMinutes(1, '', '', ''), 0);
});

function state(fields) {
  return { ...load().photosCaptureState(), ...fields };
}

const body = (ctx, uuids, fields) => {
  const result = ctx.photosCaptureBody(uuids, state(fields));
  return result && JSON.parse(JSON.stringify(result));
};

test('the request body says only what the dialog asks for', () => {
  const ctx = load();
  assert.deepEqual(body(ctx, ['a', 'b'], { dateMode: 'shift', shiftSign: -1, shiftHours: 2 }), {
    uuids: ['a', 'b'], date_mode: 'shift', shift_minutes: -120,
  });
  assert.deepEqual(body(ctx, ['a'], { dateMode: 'set', date: '1998-07-12' }), {
    uuids: ['a'], date_mode: 'set', date: '1998-07-12', time: null,
  });
  assert.deepEqual(body(ctx, ['a'], { locationMode: 'set', coordinates: '43.3, 5.4' }), {
    uuids: ['a'], location_mode: 'set', latitude: 43.3, longitude: 5.4,
  });
  assert.deepEqual(body(ctx, ['a'], { dateMode: 'restore', locationMode: 'clear' }), {
    uuids: ['a'], date_mode: 'restore', location_mode: 'clear',
  });
});

test('nothing to send while a field is incomplete or nothing changes', () => {
  const ctx = load();
  assert.equal(body(ctx, ['a'], {}), null);
  assert.equal(body(ctx, ['a'], { dateMode: 'shift' }), null);
  assert.equal(body(ctx, ['a'], { dateMode: 'set', date: '' }), null);
  assert.equal(body(ctx, ['a'], { locationMode: 'set', coordinates: 'Paris' }), null);
  // A valid date does not carry an invalid place along.
  assert.equal(body(ctx, ['a'], { dateMode: 'set', date: '1998-07-12', locationMode: 'set' }), null);
});

test('the wall clock is read off the rendered date, not the browser timezone', () => {
  const ctx = load();
  assert.deepEqual({ ...ctx.photosWallClock('2024-07-14T23:30:00+02:00') }, { date: '2024-07-14', time: '23:30' });
  assert.deepEqual({ ...ctx.photosWallClock('') }, { date: '', time: '' });
});

function tile(uuid, data) {
  return { dataset: { uuid, ...data } };
}

function app({ tiles = [], requests = [], alerts = [], response = { dated: 2, located: 0 } } = {}) {
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  const ctx = load({
    document: { getElementById: (id) => (id === 'capture-details-dialog' ? dialog : null) },
    AppAlert: {
      success: (m) => alerts.push(['success', m]),
      error: (m) => alerts.push(['error', m]),
    },
  });
  // What photos.js provides around the mixin.
  ctx.photosCountLabel = (n) => (n === 1 ? '1 photo' : `${n} photos`);
  ctx.photosJson = (method, url, payload) => {
    requests.push([method, url, JSON.parse(JSON.stringify(payload))]);
    return Promise.resolve(response);
  };
  const byUuid = Object.fromEntries(tiles.map((t) => [t.dataset.uuid, t]));
  const refreshed = [];
  const component = {
    ...ctx.photosCaptureDetailsMixin(),
    selection: [],
    selectionBusy: false,
    propertiesUuid: null,
    _tile: (uuid) => byUuid[uuid] || null,
    closeSelectionMenu() {},
    clearSelection() { this.selection = []; },
    _refresh: (targets) => refreshed.push(targets),
  };
  return { component, dialog, refreshed };
}

test('the selection may be corrected only when every tile says so', () => {
  const { component } = app({
    tiles: [tile('a', { canEditDetails: '1' }), tile('b', { canEditDetails: '0' })],
  });
  component.selection = ['a'];
  assert.equal(component.selectionEditsDetails(), true);
  component.selection = ['a', 'b'];
  assert.equal(component.selectionEditsDetails(), false);
  component.selection = [];
  assert.equal(component.selectionEditsDetails(), false);
});

test('one photo opens on its own date and time, a batch keeps each one its time', () => {
  const tiles = [
    tile('a', { canEditDetails: '1', takenAt: '' }),
    tile('b', { canEditDetails: '1', takenAt: '2024-07-14T10:05:00+02:00' }),
  ];
  const { component, dialog } = app({ tiles });

  component.openCaptureDialog(['b']);
  assert.equal(dialog.open, true);
  assert.equal(component.captureDialog.date, '2024-07-14');
  assert.equal(component.captureDialog.time, '10:05');

  component.openCaptureDialog(['a', 'b']);
  assert.equal(component.captureDialog.date, '2024-07-14');
  assert.equal(component.captureDialog.time, '');
});

test('a photo the viewer may not correct opens nothing', () => {
  const { component, dialog } = app({ tiles: [tile('a', { canEditDetails: '0' })] });
  component.openCaptureDialog(['a']);
  assert.equal(dialog.open, false);
});

test('saving posts the correction, then refreshes the timeline', async () => {
  const requests = [];
  const alerts = [];
  const tiles = [tile('a', { canEditDetails: '1' }), tile('b', { canEditDetails: '1' })];
  const { component, dialog, refreshed } = app({ tiles, requests, alerts });
  component.selection = ['a', 'b'];
  component.openCaptureDialog(['a', 'b']);
  component.captureDialog.dateMode = 'shift';
  component.captureDialog.shiftHours = 2;

  await component.saveCaptureDetails();

  assert.deepEqual(requests, [[
    'POST', '/api/v1/photos/capture-details', { uuids: ['a', 'b'], date_mode: 'shift', shift_minutes: 120 },
  ]]);
  assert.equal(dialog.open, false);
  assert.deepEqual(component.selection, []);
  assert.deepEqual(alerts, [['success', 'Updated 2 photos']]);
  assert.deepEqual(refreshed.map((t) => Array.from(t)), [['photos-nav', 'photos-content']]);
});

test('a shift that skipped undated photos says so', async () => {
  const alerts = [];
  const tiles = [tile('a', { canEditDetails: '1' }), tile('b', { canEditDetails: '1' })];
  const { component } = app({ tiles, alerts, response: { dated: 1, located: 0 } });
  component.openCaptureDialog(['a', 'b']);
  component.captureDialog.dateMode = 'shift';
  component.captureDialog.shiftMinutes = 30;

  await component.saveCaptureDetails();

  assert.deepEqual(alerts, [['success', 'Moved 1 photo, 1 without a date left as is']]);
});
