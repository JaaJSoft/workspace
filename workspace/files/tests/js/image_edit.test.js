const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const { imageEdit } = loadScript('workspace/files/ui/static/files/ui/js/image_edit.js');

// Values built in the vm carry its prototypes: compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

test('an edit that changes nothing is null', () => {
  assert.equal(imageEdit.normalize(0, null), null);
  assert.equal(imageEdit.normalize(360, { x: 0, y: 0, width: 1, height: 1 }), null);
});

test('a crop is stored rounded, as the server stores it', () => {
  assert.deepStrictEqual(
    plain(imageEdit.normalize(90, { x: 0.123456, y: 0, width: 0.5, height: 0.75 })),
    { rotation: 90, crop: { x: 0.1235, y: 0, width: 0.5, height: 0.75 } },
  );
});

test('a clockwise turn carries the crop with the image', () => {
  // Same case as RotateEditTests in test_image_edits.py: the two must agree.
  assert.deepStrictEqual(
    plain(imageEdit.rotate({ crop: { x: 0, y: 0, width: 0.25, height: 1 } }, 1)),
    { rotation: 90, crop: { x: 0, y: 0, width: 1, height: 0.25 } },
  );
});

test('four turns come back to the same edit, and a turn back undoes one', () => {
  const edit = { crop: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 } };
  let turned = edit;
  for (let i = 0; i < 4; i++) turned = imageEdit.rotate(turned, 1);
  assert.deepStrictEqual(plain(turned), edit);
  assert.equal(imageEdit.rotate(imageEdit.rotate(null, 1), -1), null);
});

test('equal ignores key order and the absent defaults', () => {
  assert.ok(imageEdit.equal(null, { rotation: 0 }));
  assert.ok(imageEdit.equal(
    { crop: { height: 0.5, width: 1, y: 0, x: 0 }, rotation: 90 },
    { rotation: 90, crop: { x: 0, y: 0, width: 1, height: 0.5 } },
  ));
  assert.ok(!imageEdit.equal(null, { rotation: 90 }));
});

test('the shown size swaps on a quarter turn and follows the crop', () => {
  assert.deepStrictEqual(plain(imageEdit.shownSize(4000, 3000, { rotation: 90 })),
    { width: 3000, height: 4000 });
  assert.deepStrictEqual(
    plain(imageEdit.shownSize(4000, 3000, { crop: { x: 0, y: 0, width: 0.5, height: 0.5 } })),
    { width: 2000, height: 1500 },
  );
});

test('fitScale never enlarges the image', () => {
  assert.equal(imageEdit.fitScale(400, 300, null, 1000, 1000), 1);
  assert.equal(imageEdit.fitScale(2000, 1000, null, 1000, 1000), 0.5);
  assert.equal(imageEdit.fitScale(2000, 1000, { rotation: 90 }, 1000, 1000), 0.5);
});

test('an unedited image fills its frame', () => {
  assert.deepStrictEqual(plain(imageEdit.layout(200, 100, null, 1)), {
    frameWidth: 200, frameHeight: 100, imageWidth: 200, imageHeight: 100,
    left: 0, top: 0, rotation: 0,
  });
});

test('a turned image is centred in the turned frame', () => {
  // 200 x 100 turned is 100 x 200: the image box, turned about its centre,
  // must cover the frame exactly.
  const layout = imageEdit.layout(200, 100, { rotation: 90 }, 1);
  assert.equal(layout.frameWidth, 100);
  assert.equal(layout.frameHeight, 200);
  assert.equal(layout.left + layout.imageWidth / 2, 50);
  assert.equal(layout.top + layout.imageHeight / 2, 100);
});

test('a crop shifts the image so the cropped part fills the frame', () => {
  const layout = imageEdit.layout(200, 100, { crop: { x: 0.5, y: 0, width: 0.5, height: 1 } }, 1);
  assert.equal(layout.frameWidth, 100);
  assert.equal(layout.frameHeight, 100);
  assert.equal(layout.left, -100);
  assert.equal(layout.top, 0);
});

test('moving the crop keeps it inside the image', () => {
  const rect = { x: 0.5, y: 0.5, width: 0.4, height: 0.4 };
  assert.deepStrictEqual(plain(imageEdit.dragCrop(rect, 'move', 0.5, -0.9)),
    { x: 0.6, y: 0, width: 0.4, height: 0.4 });
});

test('a corner handle moves its two sides only', () => {
  const rect = { x: 0.2, y: 0.2, width: 0.6, height: 0.6 };
  const dragged = imageEdit.dragCrop(rect, 'nw', -0.1, 0.1);
  const expected = { x: 0.1, y: 0.3, width: 0.7, height: 0.5 };
  for (const key of Object.keys(expected)) {
    assert.ok(Math.abs(dragged[key] - expected[key]) < 1e-9, key);
  }
});

test('a side never crosses the other, nor leaves the image', () => {
  const rect = { x: 0.2, y: 0.2, width: 0.6, height: 0.6 };
  const squeezed = imageEdit.dragCrop(rect, 'e', -1, 0);
  assert.equal(squeezed.x, 0.2);
  assert.ok(Math.abs(squeezed.width - imageEdit.MIN_CROP) < 1e-9);
  const stretched = imageEdit.dragCrop(rect, 's', 0, 5);
  assert.equal(stretched.y + stretched.height, 1);
});
