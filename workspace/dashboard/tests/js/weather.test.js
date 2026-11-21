'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SYMBOLS = { celsius: '°C', fahrenheit: '°F' };

function forecast({ temp = 21.4, code = 0, isDay = 1, unit = 'celsius' } = {}) {
  return {
    current: { temperature_2m: temp, weather_code: code, is_day: isDay },
    current_units: { temperature_2m: SYMBOLS[unit] },
  };
}

/**
 * A weatherWidget(unit) wired to an in-memory localStorage, a geolocation
 * that answers at once, and a fetch answering with whatever `respond`
 * returns for the requested URL.
 */
function buildWidget(unit, { storage = {}, respond = () => forecast() } = {}) {
  const requests = [];
  const ctx = loadScript('workspace/dashboard/static/dashboard/js/weather.js', {
    getUserLocale: () => 'en-US',
    getUserTimeZone: () => 'Europe/Paris',
    localStorage: {
      getItem: (key) => (key in storage ? storage[key] : null),
      setItem: (key, value) => { storage[key] = String(value); },
    },
    navigator: {
      geolocation: {
        getCurrentPosition: (ok) => ok({ coords: { latitude: 48.8566, longitude: 2.3522 } }),
      },
    },
    fetch: (url) => {
      requests.push(url);
      return Promise.resolve({ json: () => Promise.resolve(respond(url)) });
    },
  });
  const widget = ctx.weatherWidget(unit);
  widget.init();
  return { widget, requests, storage };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function requestedUnit(url) {
  return new URL(url).searchParams.get('temperature_unit');
}

test('a clear sky shows a moon when the forecast says it is night', async () => {
  const { widget } = buildWidget('celsius', { respond: () => forecast({ code: 0, isDay: 0 }) });
  await settle();
  assert.equal(widget.icon, 'moon');
});

test('a clear sky shows a sun when the forecast says it is day', async () => {
  const { widget } = buildWidget('celsius', { respond: () => forecast({ code: 1, isDay: 1 }) });
  await settle();
  assert.equal(widget.icon, 'sun');
});

test('a partly cloudy night shows a cloud with a moon', async () => {
  const { widget } = buildWidget('celsius', { respond: () => forecast({ code: 2, isDay: 0 }) });
  await settle();
  assert.equal(widget.icon, 'cloud-moon');
});

test('rain looks the same by day and by night', async () => {
  const { widget } = buildWidget('celsius', { respond: () => forecast({ code: 63, isDay: 0 }) });
  await settle();
  assert.equal(widget.icon, 'cloud-rain');
});

test('the forecast is asked for is_day and the user unit', async () => {
  const { widget, requests } = buildWidget('fahrenheit', {
    respond: () => forecast({ temp: 71.6, unit: 'fahrenheit' }),
  });
  await settle();
  assert.equal(requests.length, 1);
  const params = new URL(requests[0]).searchParams;
  assert.deepStrictEqual(params.get('current').split(','), ['temperature_2m', 'weather_code', 'is_day']);
  assert.equal(params.get('temperature_unit'), 'fahrenheit');
  assert.match(widget.label, / · 72°F$/);
});

test('a fresh reading in the same unit is served from the cache', async () => {
  const storage = {};
  buildWidget('celsius', { storage });
  await settle();
  const { widget, requests } = buildWidget('celsius', {
    storage,
    respond: () => assert.fail('the cache should have answered'),
  });
  await settle();
  assert.equal(requests.length, 0);
  assert.match(widget.label, / · 21°C$/);
});

test('a cached reading in another unit is read again', async () => {
  const storage = {};
  buildWidget('celsius', { storage });
  await settle();
  const { widget, requests } = buildWidget('fahrenheit', {
    storage,
    respond: () => forecast({ temp: 70.5, unit: 'fahrenheit' }),
  });
  await settle();
  assert.equal(requests.length, 1);
  assert.match(widget.label, / · 71°F$/);
});

test('a cached reading without day or night is read again', async () => {
  const storage = {
    dashboard_weather: JSON.stringify({ temp: 12, code: 0, ts: Date.now() }),
  };
  const { widget, requests } = buildWidget('celsius', {
    storage,
    respond: () => forecast({ code: 0, isDay: 0 }),
  });
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(widget.icon, 'moon');
});

test('changing the unit reads the weather again in the new unit', async () => {
  const { widget, requests } = buildWidget('celsius', {
    respond: (url) => forecast({
      temp: requestedUnit(url) === 'fahrenheit' ? 69.8 : 21,
      unit: requestedUnit(url),
    }),
  });
  await settle();
  widget.setUnit('fahrenheit');
  await settle();
  assert.deepStrictEqual(requests.map(requestedUnit), ['celsius', 'fahrenheit']);
  assert.match(widget.label, / · 70°F$/);
});

test('a reading that arrives after the unit changed is not shown', async () => {
  let answerCelsius;
  const { widget } = buildWidget('celsius', {
    respond: (url) => (requestedUnit(url) === 'celsius'
      ? new Promise((resolve) => { answerCelsius = () => resolve(forecast({ temp: 21 })); })
      : forecast({ temp: 69.8, unit: 'fahrenheit' })),
  });
  widget.setUnit('fahrenheit');
  await settle();
  answerCelsius();
  await settle();
  assert.match(widget.label, / · 70°F$/);
});
