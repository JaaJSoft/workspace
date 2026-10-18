// Correcting when and where photos were taken, for one photo or a whole
// selection: the Edit date & location dialog (capture_details_dialog.html).
// POST /api/v1/photos/capture-details does the work; each tile says whether
// the viewer may correct it (data-can-edit-details).

const PHOTOS_CAPTURE_API = '/api/v1/photos/capture-details';

// What a field of the dialog starts from: nothing changes until asked.
const PHOTOS_CAPTURE_KEEP = 'keep';

// "lat, lon" as typed or copied (the properties panel's own format), a
// geo: URI, or a link to OpenStreetMap or Google Maps. Returns
// { latitude, longitude }, or null when the text holds no valid position.
function photosParseCoordinates(text) {
  const value = String(text || '').trim();
  if (!value) return null;
  const number = '([-+]?\\d{1,3}(?:\\.\\d+)?)';
  const patterns = [
    // OpenStreetMap: ?mlat=..&mlon=.. (a marker) wins over #map=z/lat/lon (the view).
    new RegExp(`[?&]mlat=${number}&mlon=${number}`),
    new RegExp(`#map=\\d+(?:\\.\\d+)?/${number}/${number}`),
    // Google Maps: the dropped pin (!3d..!4d..), then the view (@lat,lon), then ?q=lat,lon.
    new RegExp(`!3d${number}!4d${number}`),
    new RegExp(`@${number},${number}`),
    new RegExp(`[?&](?:q|query|ll)=${number}(?:,|%2C)\\s*${number}`, 'i'),
    new RegExp(`^geo:${number},${number}`, 'i'),
    new RegExp(`^${number}\\s*[,; ]\\s*${number}$`),
  ];
  for (const pattern of patterns) {
    const match = value.match(pattern);
    if (!match) continue;
    const latitude = Number(match[1]);
    const longitude = Number(match[2]);
    if (Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180) return { latitude, longitude };
    return null;
  }
  return null;
}

// The signed number of minutes a shift of `days`, `hours`, `minutes`
// moves by, later when `sign` is 1 and earlier when it is -1.
function photosShiftMinutes(sign, days, hours, minutes) {
  const total = (Number(days) || 0) * 1440 + (Number(hours) || 0) * 60 + (Number(minutes) || 0);
  return sign < 0 ? -total : total;
}

// The request body the dialog's `state` asks for, or null when it asks
// for nothing the server would take.
function photosCaptureBody(uuids, state) {
  const body = { uuids };
  if (state.dateMode === 'shift') {
    const minutes = photosShiftMinutes(state.shiftSign, state.shiftDays, state.shiftHours, state.shiftMinutes);
    if (!minutes) return null;
    body.date_mode = 'shift';
    body.shift_minutes = minutes;
  } else if (state.dateMode === 'set') {
    if (!state.date) return null;
    body.date_mode = 'set';
    body.date = state.date;
    body.time = state.time || null;
  } else if (state.dateMode === 'restore') {
    body.date_mode = 'restore';
  }
  if (state.locationMode === 'set') {
    const position = photosParseCoordinates(state.coordinates);
    if (!position) return null;
    body.location_mode = 'set';
    body.latitude = position.latitude;
    body.longitude = position.longitude;
  } else if (state.locationMode === 'clear' || state.locationMode === 'restore') {
    body.location_mode = state.locationMode;
  }
  if (!body.date_mode && !body.location_mode) return null;
  return body;
}

// The wall clock of `iso` as the date and time inputs read it:
// { date: 'YYYY-MM-DD', time: 'HH:MM' }, empty strings without a date. The
// tile renders it in the viewer's timezone, the one the server reads the
// inputs in, so the string is read as is rather than through the browser's.
function photosWallClock(iso) {
  const match = String(iso || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? { date: match[1], time: match[2] } : { date: '', time: '' };
}

// `iso`'s wall clock moved by `minutes`, formatted for the preview.
function photosShiftedWallClock(iso, minutes) {
  const { date, time } = photosWallClock(iso);
  if (!date) return '';
  const at = new Date(`${date}T${time}:00Z`);
  at.setUTCMinutes(at.getUTCMinutes() + minutes);
  return at.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' });
}

function photosCaptureState() {
  return {
    uuids: [],
    // The first dated photo of the selection, as an ISO string: what the
    // shift preview and the set fields start from.
    sample: '',
    dateMode: PHOTOS_CAPTURE_KEEP,
    shiftSign: 1,
    shiftDays: 0,
    shiftHours: 0,
    shiftMinutes: 0,
    date: '',
    time: '',
    locationMode: PHOTOS_CAPTURE_KEEP,
    coordinates: '',
    saving: false,
  };
}

window.photosCaptureDetailsMixin = function photosCaptureDetailsMixin() {
  return {
    captureDialog: photosCaptureState(),

    // Whether the viewer may correct every photo of `uuids`, as the page
    // was rendered for them.
    canEditCaptureDetails(uuids) {
      return uuids.length > 0 && uuids.every((uuid) => {
        const tile = this._tile(uuid);
        return !!tile && tile.dataset.canEditDetails === '1';
      });
    },

    selectionEditsDetails() {
      return !this.selectionBusy && this.canEditCaptureDetails(this.selection);
    },

    openCaptureDialog(uuids) {
      this.closeSelectionMenu();
      if (!this.canEditCaptureDetails(uuids)) return;
      const sample = uuids
        .map((uuid) => this._tile(uuid)?.dataset.takenAt)
        .find((iso) => iso) || '';
      const parts = photosWallClock(sample);
      this.captureDialog = {
        ...photosCaptureState(),
        uuids: uuids.slice(),
        sample,
        date: parts.date,
        // One photo starts from its own time; a batch keeps each one's.
        time: uuids.length === 1 ? parts.time : '',
      };
      document.getElementById('capture-details-dialog')?.showModal();
    },

    closeCaptureDialog() {
      document.getElementById('capture-details-dialog')?.close();
    },

    captureCountLabel() {
      return photosCountLabel(this.captureDialog.uuids.length);
    },

    captureCoordinates() {
      return photosParseCoordinates(this.captureDialog.coordinates);
    },

    captureMapUrl() {
      const position = this.captureCoordinates();
      if (!position) return '';
      const { latitude, longitude } = position;
      return `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=16/${latitude}/${longitude}`;
    },

    // "14 Jul 2024, 10:00 → 14 Jul 2024, 12:00" for the first dated photo,
    // empty when the selection has none or the shift is nothing.
    captureShiftPreview() {
      const d = this.captureDialog;
      const minutes = photosShiftMinutes(d.shiftSign, d.shiftDays, d.shiftHours, d.shiftMinutes);
      if (!d.sample || !minutes) return '';
      return `${photosShiftedWallClock(d.sample, 0)} → ${photosShiftedWallClock(d.sample, minutes)}`;
    },

    captureCanSave() {
      return !this.captureDialog.saving
        && photosCaptureBody(this.captureDialog.uuids, this.captureDialog) !== null;
    },

    async saveCaptureDetails() {
      const d = this.captureDialog;
      const body = photosCaptureBody(d.uuids, d);
      if (!body || d.saving) return;
      d.saving = true;
      let result;
      try {
        result = await photosJson('POST', PHOTOS_CAPTURE_API, body);
      } catch (_) {
        window.AppAlert.error('Failed to update the photos');
        return;
      } finally {
        d.saving = false;
      }
      this.closeCaptureDialog();
      window.AppAlert.success(photosCaptureReport(body, result, d.uuids.length), { duration: 3000 });
      if (this.selection.length) this.clearSelection();
      if (this.propertiesUuid && d.uuids.includes(this.propertiesUuid)) this.reloadPropertiesPanel();
      // A date moves photos to other days and months, or out of Undated.
      this._refresh(['photos-nav', 'photos-content']);
    },
  };
};

// What the success toast says once the server answered `result` for `body`
// on `total` photos.
function photosCaptureReport(body, result, total) {
  if (body.date_mode === 'shift' && result.dated < total) {
    const skipped = total - result.dated;
    return `Moved ${photosCountLabel(result.dated)}, ${skipped} without a date left as is`;
  }
  if (body.date_mode === 'restore' || body.location_mode === 'restore') {
    return `Reading ${photosCountLabel(total)} again from the file`;
  }
  return `Updated ${photosCountLabel(total)}`;
}
