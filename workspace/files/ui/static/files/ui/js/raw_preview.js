// Camera RAW photos, shown through the JPEG preview the camera embeds: no
// browser decodes a RAW. The same reading as files/services/raw_images.py -
// the largest embedded JPEG, turned by the RAW's own orientation - so the
// viewer shows what the thumbnail shows; the two must agree.

window.rawPreview = {
  LABELS: Object.freeze(['dng', 'cr2', 'cr3', 'nef', 'arw', 'orf', 'rw2', 'pef', 'raf']),

  _TIFF_MAGICS: ['II*\0', 'MM\0*', 'IIRO', 'IIRS', 'MMOR', 'IIU\0'],
  _ORIENTATION: 0x0112,

  _ascii(bytes, start, length) {
    return String.fromCharCode(...bytes.subarray(start, start + length));
  },

  // The JPEG starting at `start`: where it ends, its pixel count (from its
  // frame header) and its APP1 segments, or null when it is not one. Walks
  // the segments rather than looking for the next FFD9: the preview's own
  // EXIF thumbnail is a JPEG nested inside it.
  _jpegAt(bytes, start) {
    const size = bytes.length;
    const app1 = [];
    let pixels = 0;
    let pos = start + 2;
    while (pos + 4 <= size) {
      if (bytes[pos] !== 0xff) return null;
      const marker = bytes[pos + 1];
      if (marker === 0xff) {
        pos += 1;
        continue;
      }
      const length = (bytes[pos + 2] << 8) | bytes[pos + 3];
      const isFrame = marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker);
      if (isFrame && pos + 9 <= size) {
        pixels = ((bytes[pos + 5] << 8) | bytes[pos + 6]) * ((bytes[pos + 7] << 8) | bytes[pos + 8]);
      }
      if (marker === 0xe1) app1.push([pos, pos + 2 + length]);
      if (marker !== 0xda) {
        pos += 2 + length;
        continue;
      }
      // Entropy-coded data: FF00 is a stuffed byte and FFD0-FFD7 a restart
      // marker, any other FFxx ends the scan (a progressive JPEG has several).
      pos += 2 + length;
      for (;;) {
        pos = bytes.indexOf(0xff, pos);
        if (pos === -1 || pos + 1 >= size) return null;
        const follower = bytes[pos + 1];
        if (follower === 0x00 || (follower >= 0xd0 && follower <= 0xd7)) {
          pos += 2;
        } else if (follower === 0xff) {
          pos += 1;
        } else if (follower === 0xd9) {
          return { start, end: pos + 2, pixels, app1 };
        } else {
          break;
        }
      }
    }
    return null;
  },

  _largestJpeg(bytes) {
    let best = null;
    let pos = this._findSoi(bytes, 0);
    while (pos !== -1) {
      const jpeg = this._jpegAt(bytes, pos);
      if (!jpeg) {
        pos = this._findSoi(bytes, pos + 3);
        continue;
      }
      if (jpeg.pixels > 0 && (!best || jpeg.pixels > best.pixels)) best = jpeg;
      pos = this._findSoi(bytes, jpeg.end);
    }
    return best;
  },

  _findSoi(bytes, from) {
    let pos = from;
    while ((pos = bytes.indexOf(0xff, pos)) !== -1) {
      if (bytes[pos + 1] === 0xd8 && bytes[pos + 2] === 0xff) return pos;
      pos += 1;
    }
    return -1;
  },

  // The orientation tag of the TIFF at `start`'s first directory, or 1.
  _tiffOrientation(bytes, start) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + start);
    const little = bytes[start] === 0x49;
    const ifd = view.getUint32(4, little);
    const count = view.getUint16(ifd, little);
    for (let i = 0; i < count; i++) {
      const entry = ifd + 2 + 12 * i;
      if (view.getUint16(entry, little) === this._ORIENTATION) {
        return view.getUint16(entry + 8, little);
      }
    }
    return 1;
  },

  // The RAW's orientation, or null when its preview's own EXIF is the one
  // to go by (a RAF keeps the full EXIF in its preview).
  _orientation(bytes) {
    if (this._TIFF_MAGICS.includes(this._ascii(bytes, 0, 4))) {
      return this._tiffOrientation(bytes, 0);
    }
    if (this._ascii(bytes, 4, 8) === 'ftypcrx ') {
      // A CR3 stores IFD0 as a TIFF in its CMT1 box.
      const head = bytes.subarray(0, 64 * 1024);
      for (let pos = 4; pos + 4 <= head.length; pos++) {
        if (this._ascii(head, pos, 4) === 'CMT1') return this._tiffOrientation(bytes, pos + 4);
      }
      return 1;
    }
    return null;
  },

  // An APP1 Exif segment holding only an orientation.
  _orientationSegment(orientation) {
    const segment = new Uint8Array(36);
    const view = new DataView(segment.buffer);
    segment.set([0xff, 0xe1], 0);
    view.setUint16(2, 34);
    segment.set([0x45, 0x78, 0x69, 0x66, 0, 0, 0x4d, 0x4d, 0, 0x2a], 4);
    view.setUint32(14, 8);
    view.setUint16(18, 1);
    view.setUint16(20, this._ORIENTATION);
    view.setUint16(22, 3);
    view.setUint32(24, 1);
    view.setUint16(28, orientation);
    view.setUint32(32, 0);
    return segment;
  },

  // The preview of the RAW in `buffer` as JPEG bytes the browser shows the
  // right way up, or null when it embeds none.
  extract(buffer) {
    const bytes = new Uint8Array(buffer);
    const jpeg = this._largestJpeg(bytes);
    if (!jpeg) return null;
    const orientation = this._orientation(bytes);
    if (orientation === null) return bytes.slice(jpeg.start, jpeg.end);
    // The preview's EXIF, when it has any, describes the preview and not the
    // photo: it goes, and the RAW's orientation takes its place.
    const parts = [bytes.subarray(jpeg.start, jpeg.start + 2)];
    if (orientation > 1) parts.push(this._orientationSegment(orientation));
    let pos = jpeg.start + 2;
    for (const [from, to] of jpeg.app1) {
      parts.push(bytes.subarray(pos, from));
      pos = to;
    }
    parts.push(bytes.subarray(pos, jpeg.end));
    const out = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  },
};
