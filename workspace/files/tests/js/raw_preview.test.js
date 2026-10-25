// The camera RAW preview the image viewer shows: the largest embedded JPEG,
// turned by the RAW's own orientation, as files/services/raw_images.py reads it.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/files/ui/static/files/ui/js/raw_preview.js');
const preview = (buffer) => {
  const out = ctx.rawPreview.extract(Uint8Array.from(buffer).buffer);
  return out && Array.from(out);
};

function segment(marker, payload) {
  const length = payload.length + 2;
  return [0xff, marker, length >> 8, length & 0xff, ...payload];
}

// A JPEG as far as the extraction reads one: no picture behind the headers,
// and entropy data with a stuffed byte and a restart marker in it.
function jpeg(width, height, app1 = null) {
  return [
    0xff, 0xd8,
    ...(app1 ? segment(0xe1, app1) : []),
    ...segment(0xc0, [8, height >> 8, height & 0xff, width >> 8, width & 0xff, 1, 1, 0x11, 0]),
    ...segment(0xda, [1, 1, 0, 0, 63, 0]),
    0x12, 0xff, 0x00, 0x34, 0xff, 0xd3, 0x56,
    0xff, 0xd9,
  ];
}

// A little-endian TIFF whose IFD0 holds only an orientation.
function tiffHeader(orientation, magic = [0x49, 0x49, 0x2a, 0x00]) {
  return [...magic, 8, 0, 0, 0, 1, 0, 0x12, 0x01, 3, 0, 1, 0, 0, 0, orientation, 0, 0, 0, 0, 0, 0, 0];
}

const EXIF_APP1 = [0x45, 0x78, 0x69, 0x66, 0, 0, 0x4d, 0x4d, 0, 0x2a];
const PREVIEW = jpeg(96, 64, EXIF_APP1);
const WITHOUT_APP1 = jpeg(96, 64);

test('the largest JPEG is the preview, whatever the order', () => {
  const raw = [...tiffHeader(1), ...PREVIEW, ...jpeg(16, 12)];

  assert.deepEqual(preview(raw), WITHOUT_APP1);
});

test('the RAW orientation replaces the preview EXIF', () => {
  const raw = [...tiffHeader(6, [0x49, 0x49, 0x52, 0x4f]), ...jpeg(16, 12), ...PREVIEW];

  const out = preview(raw);

  assert.deepEqual(out.slice(0, 4), [0xff, 0xd8, 0xff, 0xe1]);
  assert.deepEqual(out.slice(6, 12), [0x45, 0x78, 0x69, 0x66, 0, 0]);
  // The orientation value of the segment's only entry.
  assert.deepEqual(out.slice(30, 32), [0, 6]);
  assert.deepEqual(out.slice(38), WITHOUT_APP1.slice(2));
});

test('a RAF preview keeps its own EXIF', () => {
  const header = Array.from('FUJIFILMCCD-RAW 0201', (c) => c.charCodeAt(0));

  assert.deepEqual(preview([...header, ...PREVIEW]), PREVIEW);
});

test('a JPEG nested in the preview does not cut it short', () => {
  const nested = jpeg(96, 64, [...EXIF_APP1, ...jpeg(16, 12)]);
  const raf = Array.from('FUJIFILMCCD-RAW ', (c) => c.charCodeAt(0));

  assert.deepEqual(preview([...raf, ...nested]), nested);
});

test('a RAW without a preview gives nothing', () => {
  assert.equal(preview(tiffHeader(1)), null);
});
