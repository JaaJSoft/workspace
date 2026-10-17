// Rotation and crop of an image, the geometry files/services/image_edits.py
// stores and draws thumbnails with; the two must agree. An edit is
// { rotation, crop }: rotation turns the image clockwise in quarter turns
// (after its EXIF orientation), crop is a rectangle in fractions of the
// rotated image. The stored form is null when nothing changes.

window.imageEdit = {
  MIN_CROP: 0.02,
  FULL: Object.freeze({ x: 0, y: 0, width: 1, height: 1 }),

  _round(value) {
    return Math.round(value * 1e4) / 1e4;
  },

  _clamp(value, low, high) {
    return Math.min(Math.max(value, low), high);
  },

  // Every field present, whatever the stored value (null included).
  expand(edit) {
    return {
      rotation: (edit && edit.rotation) || 0,
      crop: { ...this.FULL, ...((edit && edit.crop) || {}) },
    };
  },

  // The stored form of a rotation and a crop: null when it changes nothing.
  normalize(rotation, crop) {
    const edit = {};
    const turned = ((rotation % 360) + 360) % 360;
    if (turned) edit.rotation = turned;
    if (crop) {
      const rect = {
        x: this._round(crop.x),
        y: this._round(crop.y),
        width: this._round(crop.width),
        height: this._round(crop.height),
      };
      if (rect.x !== 0 || rect.y !== 0 || rect.width !== 1 || rect.height !== 1) {
        edit.crop = rect;
      }
    }
    return Object.keys(edit).length ? edit : null;
  },

  // Whether two stored edits show the same thing. Compared field by field:
  // a JSON column may hand the keys back in another order.
  equal(a, b) {
    const left = this.expand(a);
    const right = this.expand(b);
    return left.rotation === right.rotation &&
      ['x', 'y', 'width', 'height'].every((key) => left.crop[key] === right.crop[key]);
  },

  // `edit` turned by `quarterTurns` more (negative: anticlockwise), its crop
  // framing the same part of the image.
  rotate(edit, quarterTurns) {
    const { rotation, crop } = this.expand(edit);
    let rect = crop;
    for (let i = 0; i < ((quarterTurns % 4) + 4) % 4; i++) {
      rect = { x: 1 - rect.y - rect.height, y: rect.x, width: rect.height, height: rect.width };
    }
    return this.normalize(rotation + 90 * quarterTurns, rect);
  },

  // The size an image of width x height (orientation applied) shows at.
  shownSize(width, height, edit) {
    const { rotation, crop } = this.expand(edit);
    const turned = rotation % 180 !== 0;
    return {
      width: Math.max(1, Math.round((turned ? height : width) * crop.width)),
      height: Math.max(1, Math.round((turned ? width : height) * crop.height)),
    };
  },

  // The largest scale, at most 1, the shown image fits a box at.
  fitScale(width, height, edit, boxWidth, boxHeight) {
    const shown = this.shownSize(width, height, edit);
    return Math.max(0, Math.min(1, boxWidth / shown.width, boxHeight / shown.height));
  },

  // Where an image of width x height goes to show `edit` at `scale`: the
  // frame clips it to the crop, and the image, sized as decoded, is placed by
  // its centre and turned about it.
  layout(width, height, edit, scale) {
    const { rotation, crop } = this.expand(edit);
    const turned = rotation % 180 !== 0;
    const turnedWidth = (turned ? height : width) * scale;
    const turnedHeight = (turned ? width : height) * scale;
    const imageWidth = width * scale;
    const imageHeight = height * scale;
    return {
      frameWidth: turnedWidth * crop.width,
      frameHeight: turnedHeight * crop.height,
      imageWidth,
      imageHeight,
      left: turnedWidth * (0.5 - crop.x) - imageWidth / 2,
      top: turnedHeight * (0.5 - crop.y) - imageHeight / 2,
      rotation,
    };
  },

  // The crop `rect` once `handle` ('move', or the sides it holds: 'n', 'se'...)
  // moved by dx, dy (fractions of the image). The rectangle stays inside the
  // image and no narrower than MIN_CROP.
  dragCrop(rect, handle, dx, dy) {
    const min = this.MIN_CROP;
    let left = rect.x;
    let top = rect.y;
    let right = rect.x + rect.width;
    let bottom = rect.y + rect.height;
    if (handle === 'move') {
      const x = this._clamp(left + dx, 0, 1 - rect.width);
      const y = this._clamp(top + dy, 0, 1 - rect.height);
      return { x, y, width: rect.width, height: rect.height };
    }
    if (handle.includes('w')) left = this._clamp(left + dx, 0, right - min);
    if (handle.includes('e')) right = this._clamp(right + dx, left + min, 1);
    if (handle.includes('n')) top = this._clamp(top + dy, 0, bottom - min);
    if (handle.includes('s')) bottom = this._clamp(bottom + dy, top + min, 1);
    return { x: left, y: top, width: right - left, height: bottom - top };
  },
};
