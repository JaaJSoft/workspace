"""Where a photo or a video says it was taken, blanked as same-size patches.

``location_patches`` reads the metadata of a file and answers the
``(offset, bytes)`` patches that overwrite its position: the GPS IFD of every
EXIF block (entries and the values they point to, zeroed), the ``GPS*``
properties of every XMP packet (their values turned to spaces), and in a
video the ISO 6709 strings of ``moov``. ``PatchedFile`` applies them to a
file handle as it is read.

Nothing moves and the size stays the same, so a Range request, a resumed
download or a zip announcing its sizes up front all keep working, and the
file is never buffered. Pixels and every other tag are left untouched.

Covered: JPEG (and the secondary images an MPF index lists), PNG, WebP,
HEIC / AVIF and MP4 / MOV. Not covered: a position stored in a video's timed
metadata track or in the media data itself (GoPro, drones); the album
renditions (``album_renditions``) drop those tracks altogether.

A malformed block is skipped and the rest of the file still patched: these
are the owner's own files, nothing here guards against a crafted one beyond
never reading out of bounds.
"""

import bisect
import re
import struct
import zlib

# The most read from one metadata block: an EXIF item, a PNG chunk, a moov
# child. Real ones are kilobytes.
MAX_BLOCK = 16 * 1024 * 1024
# The most chunks or boxes walked at one level; a PNG writes one IDAT chunk
# every 8 KB or so.
MAX_CHUNKS = 100_000

_GPS_IFD_TAG = 0x8825
_MPF_ENTRY_TAG = 0xB002
# Bytes per value, by TIFF field type.
_TYPE_SIZES = {
    1: 1,
    2: 1,
    3: 2,
    4: 4,
    5: 8,
    6: 1,
    7: 1,
    8: 2,
    9: 4,
    10: 8,
    11: 4,
    12: 8,
}

_XMP_NAMESPACE = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_EXTENSION = b"http://ns.adobe.com/xmp/extension/\x00"
_XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
# A property whose local name starts with GPS in any namespace: exif:GPSLatitude,
# exifEX:GPSAreaInformation, drone-dji:GpsLongitude... The lookbehind anchors
# a match at the start of a name, so a long run of letters is scanned once.
_XMP_ATTRIBUTE_RE = re.compile(
    rb"""(?<![\w.-])[A-Za-z][\w.-]*:[Gg][Pp][Ss][\w.-]*\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)
# The opening tag of a GPS element, attributes included; a self-closing one
# holds no value.
_XMP_ELEMENT_RE = re.compile(
    rb"<([A-Za-z][\w.-]*:[Gg][Pp][Ss][\w.-]*)(?:\s[^<>]*)?(?<!/)>"
)
_XMP_CLOSE_RE = re.compile(rb"</([A-Za-z][\w.-]*:[Gg][Pp][Ss][\w.-]*)\s*>")
_XMP_TAG_RE = re.compile(rb"<[^<>]*>")

_ISOBMFF_TOP = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot"}
_APPLE_LOCATION_KEY = b"com.apple.quicktime.location"
_QT_LOCATION = b"\xa9xyz"
_UTF8_DATA_TYPES = {1, 4}


class _Malformed(Exception):
    pass


def location_patches(handle):
    """The patches that blank the position *handle*'s file carries, in file
    order; empty when it has none or is not a format read here. Leaves
    *handle* at its start."""
    try:
        head = _read_at(handle, 0, 16)
        if head.startswith(b"\xff\xd8"):
            patches = _jpeg(handle, 0, nested=False)
        elif head.startswith(b"\x89PNG\r\n\x1a\n"):
            patches = _png(handle)
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            patches = _webp(handle)
        elif head[4:8] in _ISOBMFF_TOP:
            patches = _isobmff(handle)
        else:
            patches = []
    finally:
        handle.seek(0)
    return sorted(patches)


class PatchedFile:
    """*handle* read with *patches* applied: the same size, seekable."""

    def __init__(self, handle, patches):
        self._handle = handle
        self._patches = sorted(patches)

    @property
    def name(self):
        return getattr(self._handle, "name", "")

    def read(self, size=-1):
        start = self._handle.tell()
        return self._apply(start, self._handle.read(size))

    def _apply(self, start, data):
        end = start + len(data)
        patched = None
        for offset, patch in self._patches:
            if offset >= end:
                break
            if offset + len(patch) <= start:
                continue
            if patched is None:
                patched = bytearray(data)
            low, high = max(offset, start), min(offset + len(patch), end)
            patched[low - start : high - start] = patch[low - offset : high - offset]
        return data if patched is None else bytes(patched)

    def seek(self, offset, whence=0):
        return self._handle.seek(offset, whence)

    def tell(self):
        return self._handle.tell()

    def seekable(self):
        return True

    def readable(self):
        return True

    def close(self):
        self._handle.close()

    @property
    def closed(self):
        return getattr(self._handle, "closed", False)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self):
        while chunk := self.read(65536):
            yield chunk


def apply_patches(data, patches, base=0):
    """*data*, found at offset *base* of the file, with *patches* applied."""
    return PatchedFile(None, patches)._apply(base, data)


def _read_at(handle, offset, size):
    handle.seek(offset)
    return handle.read(size)


def _file_size(handle):
    return handle.seek(0, 2)


def _unpack(fmt, data, offset):
    try:
        return struct.unpack_from(fmt, data, offset)
    except struct.error as exc:
        raise _Malformed from exc


# ── EXIF / TIFF ──────────────────────────────────────────────


def _tiff(tiff, base):
    """Patches emptying the GPS IFD of the TIFF block *tiff*, found at file
    offset *base*: its entries, and the values longer than four bytes they
    point to, all zeroed. An IFD of no entry is still valid TIFF."""
    if tiff[:4] == b"II*\x00":
        order = "<"
    elif tiff[:4] == b"MM\x00*":
        order = ">"
    else:
        return []
    try:
        (ifd0,) = _unpack(order + "I", tiff, 4)
        (count,) = _unpack(order + "H", tiff, ifd0)
        for index in range(count):
            entry = ifd0 + 2 + 12 * index
            tag, _type, _count, value = _unpack(order + "HHII", tiff, entry)
            if tag == _GPS_IFD_TAG:
                return _empty_ifd(tiff, value, order, base)
    except _Malformed:
        pass
    return []


def _empty_ifd(tiff, ifd, order, base):
    (count,) = _unpack(order + "H", tiff, ifd)
    end = ifd + 2 + 12 * count
    if end > len(tiff):
        raise _Malformed
    patches = []
    for index in range(count):
        entry = ifd + 2 + 12 * index
        _tag, field_type, values, offset = _unpack(order + "HHII", tiff, entry)
        size = _TYPE_SIZES.get(field_type, 1) * values
        if 4 < size and offset + size <= len(tiff):
            patches.append((base + offset, bytes(size)))
    patches.append((base + ifd, bytes(end - ifd)))
    return patches


def _exif_payload(data, base):
    """Patches for an EXIF payload that may or may not start with Exif\\0\\0."""
    if data.startswith(b"Exif\x00\x00"):
        return _tiff(data[6:], base + 6)
    return _tiff(data, base)


# ── XMP ──────────────────────────────────────────────────────


def _xmp(packet, base):
    """Patches turning the value of every GPS property of the XMP text
    *packet*, found at file offset *base*, to spaces of the same length."""
    patches = []
    for match in _XMP_ATTRIBUTE_RE.finditer(packet):
        group = 1 if match.group(1) is not None else 2
        start, end = match.span(group)
        if end > start:
            patches.append((base + start, b" " * (end - start)))
    # Every closing tag found in one pass, so pairing an opener with its
    # close is a lookup: searching forward from each opener would rescan the
    # packet once per opener of a crafted file.
    closes = {}
    for match in _XMP_CLOSE_RE.finditer(packet):
        closes.setdefault(match.group(1), []).append(match.start())
    covered = 0
    for match in _XMP_ELEMENT_RE.finditer(packet):
        if match.start() < covered:
            continue
        positions = closes.get(match.group(1), [])
        index = bisect.bisect_left(positions, match.end())
        # Unclosed, the element is not well-formed XML: no reader gets a
        # value out of it.
        if index == len(positions):
            continue
        close = covered = positions[index]
        # Every text node up to the closing tag, nested rdf:Alt / rdf:li
        # values included; the tags stay, so the XML still parses.
        position = match.end()
        for tag in [*_XMP_TAG_RE.finditer(packet, position, close), None]:
            text_end = tag.start() if tag else close
            if text_end > position:
                patches.append((base + position, b" " * (text_end - position)))
            if tag:
                position = tag.end()
    return patches


# ── JPEG ─────────────────────────────────────────────────────


def _jpeg(handle, start, *, nested):
    patches = []
    mpf = []
    position = start + 2
    for _ in range(MAX_CHUNKS):
        head = _read_at(handle, position, 4)
        if len(head) < 4 or head[0] != 0xFF:
            break
        marker = head[1]
        if marker == 0xFF:
            position += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            position += 2
            continue
        # Start of scan: the compressed picture follows, no metadata after.
        if marker in (0xDA, 0xD9):
            break
        (length,) = struct.unpack(">H", head[2:4])
        if length < 2:
            break
        payload = position + 4
        if marker == 0xE1:
            segment = _read_at(handle, payload, length - 2)
            if segment.startswith(b"Exif\x00\x00"):
                patches += _tiff(segment[6:], payload + 6)
            elif segment.startswith((_XMP_NAMESPACE, _XMP_EXTENSION)):
                patches += _xmp(segment, payload)
        elif marker == 0xE2 and not nested:
            segment = _read_at(handle, payload, length - 2)
            if segment.startswith(b"MPF\x00"):
                mpf.append((segment[4:], payload + 4))
        position += 2 + length
    for index, base in mpf:
        for offset in _mpf_image_offsets(index):
            image = base + offset
            if _read_at(handle, image, 2) == b"\xff\xd8":
                patches += _jpeg(handle, image, nested=True)
    return patches


def _mpf_image_offsets(index):
    """The offsets, from the MPF header, of the images after the first one
    (a depth map, a gain map, a preview), each a JPEG with its own EXIF."""
    if index[:4] == b"II*\x00":
        order = "<"
    elif index[:4] == b"MM\x00*":
        order = ">"
    else:
        return []
    try:
        (ifd,) = _unpack(order + "I", index, 4)
        (count,) = _unpack(order + "H", index, ifd)
        for position in range(count):
            tag, _type, size, value = _unpack(
                order + "HHII", index, ifd + 2 + 12 * position
            )
            if tag == _MPF_ENTRY_TAG:
                entries = index[value : value + size]
                return [
                    _unpack(order + "I", entries, entry + 8)[0]
                    for entry in range(0, len(entries) - 15, 16)
                    if _unpack(order + "I", entries, entry + 8)[0]
                ]
    except _Malformed:
        pass
    return []


# ── PNG ──────────────────────────────────────────────────────


def _png(handle):
    patches = []
    position = 8
    for _ in range(MAX_CHUNKS):
        head = _read_at(handle, position, 8)
        if len(head) < 8:
            break
        length, kind = struct.unpack(">I4s", head)
        data_at = position + 8
        if kind in (b"eXIf", b"iTXt") and length <= MAX_BLOCK:
            data = _read_at(handle, data_at, length)
            if kind == b"eXIf":
                found = _exif_payload(data, data_at)
            else:
                found = _png_xmp(data, data_at)
            if found:
                # The chunk's CRC covers its type and data: recomputed over
                # the patched data, or a strict decoder drops the chunk.
                patched = apply_patches(data, found, data_at)
                crc = zlib.crc32(kind + patched) & 0xFFFFFFFF
                patches += [*found, (data_at + length, struct.pack(">I", crc))]
        if kind == b"IEND":
            break
        position = data_at + length + 4
    return patches


def _png_xmp(data, base):
    """An uncompressed iTXt chunk holding XMP; a compressed one is left as is."""
    keyword, _, rest = data.partition(b"\x00")
    if keyword != b"XML:com.adobe.xmp" or rest[:1] != b"\x00":
        return []
    # Compression flag and method, then the language and translated keyword.
    after = 2
    for _ in range(2):
        end = rest.find(b"\x00", after)
        if end < 0:
            return []
        after = end + 1
    text_at = len(keyword) + 1 + after
    return _xmp(data[text_at:], base + text_at)


# ── WebP ─────────────────────────────────────────────────────


def _webp(handle):
    patches = []
    end = min(struct.unpack("<I", _read_at(handle, 4, 4))[0] + 8, _file_size(handle))
    position = 12
    for _ in range(MAX_CHUNKS):
        head = _read_at(handle, position, 8)
        if len(head) < 8 or position + 8 > end:
            break
        kind, length = struct.unpack("<4sI", head)
        data_at = position + 8
        if kind in (b"EXIF", b"XMP ") and length <= MAX_BLOCK:
            data = _read_at(handle, data_at, length)
            if kind == b"EXIF":
                patches += _exif_payload(data, data_at)
            else:
                patches += _xmp(data, data_at)
        position = data_at + length + (length & 1)
    return patches


# ── ISO base media: HEIC, AVIF, MP4, MOV ─────────────────────


def _boxes(handle, start, end):
    """``(type, box offset, payload offset, box end)`` of each box between
    *start* and *end*."""
    position = start
    for _ in range(MAX_CHUNKS):
        if position + 8 > end:
            return
        head = _read_at(handle, position, 16)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head[:8])
        payload = position + 8
        if size == 1:
            if len(head) < 16:
                return
            (size,) = struct.unpack(">Q", head[8:16])
            payload += 8
        elif size == 0:
            size = end - position
        if size < payload - position or position + size > end:
            return
        yield kind, position, payload, position + size
        position += size


def _memory_boxes(data, start, end):
    """:func:`_boxes` over bytes already read."""
    position = start
    while position + 8 <= end:
        size, kind = _unpack(">I4s", data, position)
        payload = position + 8
        if size == 1:
            (size,) = _unpack(">Q", data, position + 8)
            payload += 8
        elif size == 0:
            size = end - position
        if size < payload - position or position + size > end:
            return
        yield kind, payload, position + size
        position += size


def _isobmff(handle):
    patches = []
    size = _file_size(handle)
    for kind, _, payload, end in _boxes(handle, 0, size):
        try:
            if kind == b"meta" and end - payload <= MAX_BLOCK:
                patches += _heif_meta(handle, payload, end)
            elif kind == b"moov":
                patches += _moov(handle, payload, end)
            elif kind == b"uuid" and end - payload <= MAX_BLOCK:
                data = _read_at(handle, payload, end - payload)
                if data[:16] == _XMP_UUID:
                    patches += _xmp(data[16:], payload + 16)
        except _Malformed, IndexError:
            continue
    return patches


def _heif_meta(handle, start, end):
    """The EXIF and XMP items of a HEIF ``meta`` box, found through
    ``iinf`` (what each item is) and ``iloc`` (where its bytes are)."""
    meta = _read_at(handle, start, end - start)
    exif_ids, xmp_ids, locations, idat = set(), set(), {}, None
    for kind, payload, box_end in _memory_boxes(meta, 4, len(meta)):
        if kind == b"iinf":
            exif_ids, xmp_ids = _iinf(meta, payload, box_end)
        elif kind == b"iloc":
            locations = _iloc(meta, payload)
        elif kind == b"idat":
            idat = start + payload
    patches = []
    for item_id in exif_ids | xmp_ids:
        extents = locations.get(item_id)
        if not extents:
            continue
        method, ranges = extents
        if method == 1:
            if idat is None:
                continue
            ranges = [(idat + offset, length) for offset, length in ranges]
        elif method != 0:
            continue
        if sum(length for _, length in ranges) > MAX_BLOCK:
            continue
        data, spans = b"", []
        for offset, length in ranges:
            spans.append((len(data), offset, length))
            data += _read_at(handle, offset, length)
        if item_id in exif_ids:
            if len(data) < 4:
                continue
            (skip,) = _unpack(">I", data, 0)
            found = _tiff(data[4 + skip :], 4 + skip)
        else:
            found = _xmp(data, 0)
        patches += _to_file_offsets(found, spans)
    return patches


def _to_file_offsets(patches, spans):
    """*patches* given at offsets of an item's concatenated extents, split
    at the extent boundaries and moved to where each extent sits in the
    file."""
    moved = []
    for offset, data in patches:
        for logical, absolute, length in spans:
            low, high = max(offset, logical), min(offset + len(data), logical + length)
            if low < high:
                moved.append(
                    (absolute + low - logical, data[low - offset : high - offset])
                )
    return moved


def _iinf(meta, start, end):
    version = meta[start]
    count_format = ">H" if version == 0 else ">I"
    first = start + 4 + struct.calcsize(count_format)
    exif_ids, xmp_ids = set(), set()
    for kind, payload, box_end in _memory_boxes(meta, first, end):
        if kind != b"infe" or meta[payload] < 2:
            continue
        version = meta[payload]
        position = payload + 4
        if version == 2:
            (item_id,) = _unpack(">H", meta, position)
            position += 2
        else:
            (item_id,) = _unpack(">I", meta, position)
            position += 4
        item_type = meta[position + 2 : position + 6]
        if item_type == b"Exif":
            exif_ids.add(item_id)
        elif item_type == b"mime":
            # Item name, then content type: XMP is application/rdf+xml.
            fields = meta[position + 6 : box_end].split(b"\x00")
            if len(fields) > 1 and fields[1] == b"application/rdf+xml":
                xmp_ids.add(item_id)
    return exif_ids, xmp_ids


def _iloc(meta, start):
    """``{item id: (construction method, [(offset, length), ...])}``."""
    version = meta[start]
    position = start + 4
    sizes = meta[position]
    offset_size, length_size = sizes >> 4, sizes & 0x0F
    sizes = meta[position + 1]
    base_size = sizes >> 4
    index_size = sizes & 0x0F if version in (1, 2) else 0
    position += 2
    count_format = ">I" if version == 2 else ">H"
    (count,) = _unpack(count_format, meta, position)
    position += struct.calcsize(count_format)
    items = {}
    for _ in range(min(count, MAX_CHUNKS)):
        (item_id,) = _unpack(count_format, meta, position)
        position += struct.calcsize(count_format)
        method = 0
        if version in (1, 2):
            (method,) = _unpack(">H", meta, position)
            method &= 0x0F
            position += 2
        position += 2  # data reference index
        base, position = _uint(meta, position, base_size)
        (extent_count,) = _unpack(">H", meta, position)
        position += 2
        extents = []
        for _ in range(extent_count):
            _, position = _uint(meta, position, index_size)
            offset, position = _uint(meta, position, offset_size)
            length, position = _uint(meta, position, length_size)
            extents.append((base + offset, length))
        items[item_id] = (method, extents)
    return items


def _uint(data, position, size):
    if size == 0:
        return 0, position
    if size not in (4, 8):
        raise _Malformed
    (value,) = _unpack(">I" if size == 4 else ">Q", data, position)
    return value, position + size


# ── Video metadata in moov ───────────────────────────────────


def _moov(handle, start, end):
    patches = []
    for kind, _, payload, box_end in _boxes(handle, start, end):
        if kind == b"udta":
            patches += _udta(handle, payload, box_end)
        elif kind == b"meta":
            patches += _qt_meta(handle, payload, box_end)
    return patches


def _udta(handle, start, end):
    patches = []
    for kind, box, payload, box_end in _boxes(handle, start, end):
        if box_end - payload > MAX_BLOCK:
            continue
        if kind in (_QT_LOCATION, b"loci"):
            # Turned into a free box, emptied: every reader skips it, where
            # zeroed coordinates would read as a position off Africa.
            patches += [(box + 4, b"free"), (payload, bytes(box_end - payload))]
        elif kind == b"XMP_":
            patches += _xmp(_read_at(handle, payload, box_end - payload), payload)
        elif kind == b"meta":
            patches += _qt_meta(handle, payload, box_end)
    return patches


def _qt_meta(handle, start, end):
    """A ``meta`` box: Apple's ``mdta`` keys and their ``ilst`` values, or
    an iTunes-style ``ilst`` holding ``©xyz``."""
    if end - start > MAX_BLOCK:
        return []
    meta = _read_at(handle, start, end - start)
    # A QuickTime meta box starts with its children; an MP4 one is a full
    # box, four bytes of version and flags first.
    first = 0 if meta[4:8] in (b"hdlr", b"keys", b"ilst") else 4
    location_keys = set()
    ilst = None
    for kind, payload, box_end in _memory_boxes(meta, first, len(meta)):
        if kind == b"keys":
            location_keys = _location_keys(meta, payload, box_end)
        elif kind == b"ilst":
            ilst = (payload, box_end)
    if ilst is None:
        return []
    patches = []
    for kind, payload, box_end in _memory_boxes(meta, *ilst):
        key = struct.unpack(">I", kind)[0]
        if kind != _QT_LOCATION and key not in location_keys:
            continue
        for data_kind, data_payload, data_end in _memory_boxes(meta, payload, box_end):
            if data_kind != b"data" or data_end - data_payload <= 8:
                continue
            (data_type,) = _unpack(">I", meta, data_payload)
            value_at = data_payload + 8
            length = data_end - value_at
            filler = b" " * length if data_type in _UTF8_DATA_TYPES else bytes(length)
            patches.append((start + value_at, filler))
    return patches


def _location_keys(meta, start, end):
    """The 1-based indexes of the ``com.apple.quicktime.location.*`` keys."""
    (count,) = _unpack(">I", meta, start + 4)
    position = start + 8
    keys = set()
    for index in range(1, min(count, MAX_CHUNKS) + 1):
        if position + 8 > end:
            break
        (size,) = _unpack(">I", meta, position)
        if size < 8:
            break
        if meta[position + 8 : position + size].startswith(_APPLE_LOCATION_KEY):
            keys.add(index)
        position += size
    return keys
