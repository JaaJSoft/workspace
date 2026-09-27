// Photos: face grouping - the People tab, a person's page, the naming,
// "People in this photo" and merge dialogs. Spread into photosApp()
// (photos.js), so it holds methods only: a getter would be frozen by the
// spread.
//
// A "card" is what the People grid shows: a named person gathering every
// cluster of theirs ({ uuid: null, clusters: [...], person: {...} }) or an
// unnamed cluster ({ uuid, clusters: [uuid], person: null }).

const FACES_API = '/api/v1/photos';
const CLUSTER_MENU_WIDTH = 224;
const CLUSTER_MENU_HEIGHT = 260;
const FACES_POLL_MS = 10000;

function facesJson(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (_) {
    return null;
  }
}

// Rejects with an Error whose `data` is the parsed error body, when there is
// one: a refused merge names the people to choose between there.
async function facesRequest(url, { method = 'GET', body } = {}) {
  const response = await fetch(url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCSRFToken(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let data = null;
    let detail = '';
    try {
      data = await response.json();
      detail = data.detail || Object.values(data).flat().join(' ');
    } catch (_) {
      // Not JSON: the status says enough.
    }
    const error = new Error(detail || `Request failed (${response.status})`);
    error.data = data;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

// "0:42", "12:05", "1:02:05": a moment of a video, as its player shows it.
function faceTimeLabel(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const s = String(total % 60).padStart(2, '0');
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

function openInViewer(uuid, name, type, at) {
  window.dispatchEvent(new CustomEvent('open-file-viewer', {
    detail: { uuid, name, type, at: at == null ? null : at },
  }));
}

// Without a query, the named people then every other contact: a picker
// opened empty lists who can be picked.
function personsUrl(query) {
  const q = (query || '').trim();
  return q ? `${FACES_API}/persons?q=${encodeURIComponent(q)}` : `${FACES_API}/persons?contacts=1`;
}

// What a merge may take in: every unnamed cluster, and one entry per named
// person - their largest cluster, the list being largest first - since the
// others of theirs are that same person already. Never the target, nor
// another cluster of the target's own person.
function mergeCandidates(clusters, target) {
  const seen = new Set(target.person ? [target.person] : []);
  return clusters.filter((c) => {
    if (c.uuid === target.uuid) return false;
    if (!c.person) return true;
    if (seen.has(c.person)) return false;
    seen.add(c.person);
    return true;
  });
}

function photoCount(count) {
  return `${count} photo${count === 1 ? '' : 's'}`;
}

window.photosFacesMixin = function photosFacesMixin() {
  return {
    facesSaving: false,
    clusterMenu: { open: false, x: 0, y: 0, cluster: null },
    nameDialog: { target: null, query: '', results: [], loading: false, saving: false },
    _nameGeneration: 0,
    facesDialog: {
      photo: null, faces: [], clusters: [], persons: [], results: null, query: '',
      loading: false, busy: false, picking: null, changed: false,
    },
    _facesGeneration: 0,
    mergeDialog: {
      target: null, clusters: [], selected: [], loading: false, saving: false, choices: null, person: null,
    },

    // Both read from the page each time: a sidebar navigation swaps the
    // content they are embedded in.
    facesEnabled() {
      return facesJson('photos-faces-enabled') === true;
    },

    // The card of the person or cluster on screen, or null.
    currentCluster() {
      return facesJson('photos-cluster-data');
    },

    // Rejected when no answer came: the content-loading veil must drop even
    // when the caller swallows the error.
    _reloadView(url = window.location.href) {
      return this.$ajax(url, { targets: ['photos-nav', 'photos-content'], focus: false }).catch((err) => {
        this.contentLoading = false;
        throw err;
      });
    },

    _peopleUrl() {
      return '/photos/people';
    },

    _isOnPageOf(card) {
      const current = this.currentCluster();
      return !!current && current.clusters[0] === card.clusters[0];
    },

    // ── Turning it on and off ───────────────────────────

    async setFacesEnabled(enabled) {
      this.facesSaving = true;
      try {
        await facesRequest('/api/v1/settings/photos/faces_enabled', {
          method: 'PUT',
          body: { value: enabled },
        });
        await this._reloadView(this._peopleUrl());
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not change face grouping');
      } finally {
        this.facesSaving = false;
      }
    },

    async turnFacesOff() {
      const ok = await AppDialog.confirm({
        title: 'Turn off face grouping',
        message: 'Every face, every group of people and every face picture is deleted right away. Your photos and your contacts are not touched. Turning it back on reads your photos again from the start.',
        okLabel: 'Turn off and delete',
        okClass: 'btn-error',
        icon: 'power-off',
        iconClass: 'bg-error/10 text-error',
      });
      if (ok) await this.setFacesEnabled(false);
    },

    // ── Card menu and actions ───────────────────────────

    openClusterMenu(event, card, anchor) {
      let x = event.clientX;
      let y = event.clientY;
      if (anchor) {
        const rect = anchor.getBoundingClientRect();
        x = rect.right - CLUSTER_MENU_WIDTH;
        y = rect.bottom + 4;
      }
      x = Math.max(4, Math.min(x, window.innerWidth - CLUSTER_MENU_WIDTH - 4));
      y = Math.max(4, Math.min(y, window.innerHeight - CLUSTER_MENU_HEIGHT - 4));
      const cover = card.querySelector('img');
      const data = card.dataset;
      this.clusterMenu = {
        open: true,
        x,
        y,
        cluster: {
          uuid: data.cluster || null,
          clusters: (data.clusters || '').split(',').filter(Boolean),
          person: data.person
            ? { uuid: data.person, name: data.personName, url: data.personUrl }
            : null,
          hidden: data.hidden === '1',
          cover_url: cover ? cover.getAttribute('src') : null,
        },
      };
    },

    closeClusterMenu() {
      this.clusterMenu.open = false;
    },

    _patchClusters(card, body) {
      return Promise.all(card.clusters.map((uuid) => facesRequest(
        `${FACES_API}/clusters/${uuid}`, { method: 'PATCH', body },
      )));
    },

    async runClusterAction(action, card) {
      this.closeClusterMenu();
      if (document.activeElement) document.activeElement.blur();
      if (!card) return;
      const onItsPage = this._isOnPageOf(card);
      try {
        switch (action) {
          case 'hide':
          case 'show':
            await this._patchClusters(card, { hidden: action === 'hide' });
            await this._reloadView();
            break;
          case 'name':
            await this.openNameDialog(card);
            break;
          case 'avatar':
            // The first cluster of a person is the one whose cover stands
            // for them.
            await facesRequest(`${FACES_API}/clusters/${card.clusters[0]}/avatar`, { method: 'POST' });
            window.AppAlert.success(`Contact photo of ${card.person.name} updated`, { duration: 2500 });
            break;
          case 'unname': {
            const ok = await AppDialog.confirm({
              title: 'Remove name',
              message: `These faces are no longer ${card.person.name}. The contact itself stays in People.`,
              okLabel: 'Remove name',
              okClass: 'btn-error',
              icon: 'user-minus',
              iconClass: 'bg-error/10 text-error',
            });
            if (!ok) return;
            await this._patchClusters(card, { person: null });
            await this._reloadView(onItsPage ? this._peopleUrl() : window.location.href);
            break;
          }
          case 'merge':
            await this.openMergeDialog(card);
            break;
          case 'ungroup': {
            const ok = await AppDialog.confirm({
              title: 'Ungroup',
              message: 'This person leaves the list, and their faces stay out of automatic grouping. They wait in Review, under Unassigned, until you say who they are.',
              okLabel: 'Ungroup',
              okClass: 'btn-error',
              icon: 'ungroup',
              iconClass: 'bg-error/10 text-error',
            });
            if (!ok) return;
            await facesRequest(`${FACES_API}/clusters/${card.uuid}`, { method: 'DELETE' });
            await this._reloadView(onItsPage ? this._peopleUrl() : window.location.href);
            break;
          }
        }
      } catch (err) {
        window.AppAlert.error(err.message || 'Something went wrong');
      }
    },

    // ── Naming dialog ───────────────────────────────────

    async openNameDialog(card) {
      const target = { ...card, clusters: card.clusters || [card.uuid] };
      this.nameDialog = { target, query: '', results: [], loading: false, saving: false };
      document.getElementById('name-person-dialog').showModal();
      await this.searchNames();
    },

    // Race-protected: an older answer never replaces a newer one.
    async searchNames() {
      const generation = ++this._nameGeneration;
      this.nameDialog.loading = true;
      try {
        const results = await facesRequest(personsUrl(this.nameDialog.query));
        if (generation === this._nameGeneration) this.nameDialog.results = results;
      } catch (_) {
        if (generation === this._nameGeneration) this.nameDialog.results = [];
      } finally {
        if (generation === this._nameGeneration) this.nameDialog.loading = false;
      }
    },

    // Offered unless a contact already has exactly that name.
    canCreateName() {
      const name = this.nameDialog.query.trim().toLowerCase();
      return !!name && !this.nameDialog.results.some((r) => r.name.toLowerCase() === name);
    },

    closeNameDialog() {
      document.getElementById('name-person-dialog').close();
    },

    async chooseName(person) {
      await this._name(async (target) => {
        await this._patchClusters(target, { person: person.uuid });
        return person.uuid;
      });
    },

    async createName() {
      const name = this.nameDialog.query.trim();
      if (!name) return;
      await this._name(async (target) => {
        const [first, ...rest] = target.clusters;
        const named = await facesRequest(`${FACES_API}/clusters/${first}`, {
          method: 'PATCH',
          body: { new_person: name },
        });
        await this._patchClusters({ clusters: rest }, { person: named.person });
        return named.person;
      });
    },

    async _name(apply) {
      const target = this.nameDialog.target;
      if (!target) return;
      this.nameDialog.saving = true;
      try {
        const personUuid = await apply(target);
        this.closeNameDialog();
        // On the page of what was just named, follow it to its new name.
        await this._reloadView(
          this._isOnPageOf(target) ? `/photos?person=${encodeURIComponent(personUuid)}` : window.location.href,
        );
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not name this person');
      } finally {
        this.nameDialog.saving = false;
      }
    },

    // ── Merge dialog ────────────────────────────────────

    async openMergeDialog(card) {
      this.mergeDialog = {
        target: card, clusters: [], selected: [], loading: true, saving: false, choices: null, person: null,
      };
      document.getElementById('merge-clusters-dialog').showModal();
      try {
        const clusters = await facesRequest(`${FACES_API}/clusters`);
        const target = clusters.find((c) => c.uuid === card.uuid);
        if (target) this.mergeDialog.target = target;
        this.mergeDialog.clusters = mergeCandidates(clusters, target || card);
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not load people');
      } finally {
        this.mergeDialog.loading = false;
      }
    },

    isMergeSelected(cluster) {
      return this.mergeDialog.selected.includes(cluster.uuid);
    },

    toggleMergeSelection(cluster) {
      const selected = this.mergeDialog.selected;
      this.mergeDialog.selected = selected.includes(cluster.uuid)
        ? selected.filter((uuid) => uuid !== cluster.uuid)
        : [...selected, cluster.uuid];
      // A new selection may name other people: ask again if it does.
      this.mergeDialog.choices = null;
      this.mergeDialog.person = null;
    },

    closeMergeDialog() {
      document.getElementById('merge-clusters-dialog').close();
    },

    async submitMerge() {
      const target = this.mergeDialog.target;
      if (!target || !this.mergeDialog.selected.length) return;
      this.mergeDialog.saving = true;
      const body = { clusters: this.mergeDialog.selected };
      if (this.mergeDialog.person) body.person = this.mergeDialog.person;
      try {
        await facesRequest(`${FACES_API}/clusters/${target.uuid}/merge`, { method: 'POST', body });
        this.closeMergeDialog();
        await this._reloadView();
      } catch (err) {
        const persons = err.data && err.data.persons;
        if (persons && persons.length) {
          // Named after different people: the user picks the name to keep.
          this.mergeDialog.choices = persons;
          this.mergeDialog.person = persons[0].uuid;
        } else {
          window.AppAlert.error(err.message || 'Could not merge');
        }
      } finally {
        this.mergeDialog.saving = false;
      }
    },

    // ── "People in this photo" ──────────────────────────

    async openFacesDialog(photo) {
      this.closeCtxMenu();
      this.facesDialog = {
        photo, faces: [], clusters: [], persons: [], results: null, query: '',
        loading: true, busy: false, picking: null, changed: false,
      };
      document.getElementById('photo-faces-dialog').showModal();
      try {
        await this._loadFacesDialog();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not load the faces');
      } finally {
        this.facesDialog.loading = false;
      }
    },

    async _loadFacesDialog() {
      const photo = this.facesDialog.photo;
      const [faces, clusters, persons] = await Promise.all([
        facesRequest(`${FACES_API}/files/${photo.uuid}/faces`),
        facesRequest(`${FACES_API}/clusters`),
        facesRequest(personsUrl('')),
      ]);
      this.facesDialog.faces = faces;
      this.facesDialog.clusters = clusters;
      this.facesDialog.persons = persons;
    },

    clusterOf(face) {
      return this.facesDialog.clusters.find((c) => c.uuid === face.cluster) || null;
    },

    facesDialogIsVideo() {
      return !!this.facesDialog.photo && this.facesDialog.photo.mediaType === 'video';
    },

    // When in the video a face was seen; empty for a photo's.
    faceTime(face) {
      return face.timestamp == null ? '' : faceTimeLabel(face.timestamp);
    },

    watchFace(face) {
      const video = this.facesDialog.photo;
      this.closeFacesDialog();
      openInViewer(video.uuid, video.name, video.type, face.timestamp);
    },

    faceLabel(face) {
      const cluster = this.clusterOf(face);
      if (cluster && cluster.person_name) return cluster.person_name;
      if (cluster) return `Unnamed, ${photoCount(cluster.photo_count)}`;
      if (face.cluster) return 'Hidden person';
      if (face.assignment === 'rejected') return 'Left out of grouping';
      if (face.assignment === 'hidden') return 'Hidden face';
      return 'Not grouped yet';
    },

    // The people other faces of this photo already are: never offered.
    _personsInPhoto(face) {
      return new Set(
        this.facesDialog.faces
          .filter((f) => f.uuid !== face.uuid)
          .map((f) => this.clusterOf(f))
          .filter((c) => c && c.person)
          .map((c) => c.person),
      );
    },

    pickablePersons(face) {
      const own = this.clusterOf(face);
      const taken = this._personsInPhoto(face);
      const source = this.facesDialog.results || this.facesDialog.persons;
      return source.filter((p) => !taken.has(p.uuid) && !(own && own.person === p.uuid));
    },

    // Unnamed clusters, while no name is typed: someone who has no name yet.
    pickableClusters(face) {
      if (this.facesDialog.query.trim()) return [];
      const taken = new Set(
        this.facesDialog.faces.filter((f) => f.uuid !== face.uuid && f.cluster).map((f) => f.cluster),
      );
      return this.facesDialog.clusters.filter(
        (c) => !c.person && c.uuid !== face.cluster && !taken.has(c.uuid),
      );
    },

    async searchFacePersons() {
      const query = this.facesDialog.query.trim();
      const generation = ++this._facesGeneration;
      if (!query) {
        this.facesDialog.results = null;
        return;
      }
      try {
        const results = await facesRequest(personsUrl(query));
        if (generation === this._facesGeneration) this.facesDialog.results = results;
      } catch (_) {
        if (generation === this._facesGeneration) this.facesDialog.results = [];
      }
    },

    canCreateFacePerson() {
      const name = this.facesDialog.query.trim().toLowerCase();
      const results = this.facesDialog.results || [];
      return !!name && !results.some((r) => r.name.toLowerCase() === name);
    },

    togglePicker(face) {
      const opening = this.facesDialog.picking !== face.uuid;
      this.facesDialog.picking = opening ? face.uuid : null;
      this.facesDialog.query = '';
      this.facesDialog.results = null;
    },

    async _correctFace(face, request) {
      this.facesDialog.busy = true;
      try {
        await request();
        await this._loadFacesDialog();
        this.facesDialog.picking = null;
        this.facesDialog.query = '';
        this.facesDialog.results = null;
        this.facesDialog.changed = true;
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not correct the face');
      } finally {
        this.facesDialog.busy = false;
      }
    },

    _patchFace(face, body) {
      return this._correctFace(face, () => facesRequest(
        `${FACES_API}/faces/${face.uuid}`, { method: 'PATCH', body },
      ));
    },

    confirmFace(face) {
      return this._patchFace(face, { assignment: 'confirmed' });
    },

    rejectFace(face) {
      return this._patchFace(face, { cluster: null });
    },

    assignFace(face, cluster) {
      return this._patchFace(face, { cluster: cluster.uuid });
    },

    assignFaceToPerson(face, person) {
      return this._patchFace(face, { to_person: person.uuid });
    },

    newPersonForFace(face) {
      const name = this.facesDialog.query.trim();
      if (!name) return null;
      return this._patchFace(face, { new_person: name });
    },

    // Someone who has no cluster yet, and no name either: the face starts
    // an unnamed cluster, confirmed.
    startCluster(face) {
      return this._correctFace(face, () => facesRequest(`${FACES_API}/clusters`, {
        method: 'POST',
        body: { face: face.uuid },
      }));
    },

    closeFacesDialog() {
      document.getElementById('photo-faces-dialog').close();
    },

    // A correction can move the photo in or out of the person on screen.
    facesDialogClosed() {
      if (this.facesDialog.changed && (this.currentCluster() || document.getElementById('people-grid'))) {
        this._reloadView();
      }
    },

    // ── A person's page ─────────────────────────────────

    async _faceInCurrentCluster(photo) {
      const current = this.currentCluster();
      if (!current) return null;
      const faces = await facesRequest(`${FACES_API}/files/${photo.uuid}/faces`);
      return faces.find((f) => current.clusters.includes(f.cluster)) || null;
    },

    async useAsCover(photo) {
      this.closeCtxMenu();
      try {
        const face = await this._faceInCurrentCluster(photo);
        if (!face) return;
        await facesRequest(`${FACES_API}/clusters/${face.cluster}`, {
          method: 'PATCH',
          body: { cover: face.uuid },
        });
        await this._offerCoverToContact(this.currentCluster().person, face.cluster);
        await this._reloadView();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not change the cover');
      }
    },

    // The new cover of a named person may become their contact photo too:
    // at once when they have none, after asking when they do - it may be a
    // real photo, or a group address book's, seen by everyone in it.
    async _offerCoverToContact(person, clusterUuid) {
      if (!person) return;
      if (person.has_avatar) {
        const ok = await AppDialog.confirm({
          title: 'Update the contact photo too?',
          message: `${person.name} already has a photo in People. Replace it with this face?`,
          okLabel: 'Replace',
          icon: 'circle-user-round',
        });
        if (!ok) return;
      }
      await facesRequest(`${FACES_API}/clusters/${clusterUuid}/avatar`, { method: 'POST' });
      window.AppAlert.success(`Also the contact photo of ${person.name} in People`, { duration: 2500 });
    },

    async _rejectFace(photo) {
      const face = await this._faceInCurrentCluster(photo);
      if (!face) return;
      await facesRequest(`${FACES_API}/faces/${face.uuid}`, { method: 'PATCH', body: { cluster: null } });
    },

    async rejectFromCluster(photo) {
      this.closeCtxMenu();
      try {
        await this._rejectFace(photo);
        await this._reloadView();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not remove the photo');
      }
    },

    // One photo after the other: each is read before its face is detached,
    // and a failure leaves the rest of the selection to go on.
    async rejectSelectionFromCluster() {
      this.closeSelectionMenu();
      if (!this.currentCluster() || this.selectionBusy) return;
      const uuids = this.selection.slice();
      this.selectionBusy = true;
      let failed = 0;
      for (const uuid of uuids) {
        try {
          await this._rejectFace({ uuid });
        } catch (_) {
          failed += 1;
        }
      }
      this.selectionBusy = false;
      if (failed) window.AppAlert.error(`Could not remove ${failed === 1 ? '1 photo' : `${failed} photos`}`);
      await this._reloadView().catch((err) => {
        window.AppAlert.error(err.message || 'Could not refresh the page');
      });
    },
  };
};

// The progress badge of the People tab: follows the analysis, and reloads
// the grid once every photo has been read.
window.facesProgress = function facesProgress(analyzed, total) {
  return {
    analyzed,
    total,
    _timer: null,

    init() {
      this._timer = setInterval(() => this.poll(), FACES_POLL_MS);
    },

    destroy() {
      clearInterval(this._timer);
    },

    async poll() {
      let status;
      try {
        status = await facesRequest(`${FACES_API}/faces/status`);
      } catch (_) {
        return;
      }
      // The total moves too: an upload or a deletion while the page is open.
      this.analyzed = status.analyzed;
      this.total = status.total;
      if (status.analyzed >= status.total) {
        clearInterval(this._timer);
        this.$ajax(window.location.href, { targets: ['photos-nav', 'photos-content'], focus: false });
      }
    },
  };
};

// ── Face boards ─────────────────────────────────────
// A board is a grid of face tiles the user picks from, then corrects in one
// go from the bar under it: the review queues, the hidden faces, a person's
// faces. Every correction goes through the batch endpoint, whose undo token
// the toast offers back.

const FACE_ACTIONS_BATCH = 500;

const FACE_DONE = {
  confirm: 'confirmed',
  reject: 'taken out',
  hide: 'hidden',
  unhide: 'unhidden',
  assign: 'moved',
};

function faceCount(count) {
  return `${count} face${count === 1 ? '' : 's'}`;
}

// The bulk actions every face of the selection offers, in the registry's
// order. `lists` holds the registry's answer for each selected face.
function faceSelectionActions(lists) {
  if (!lists.length) return [];
  return lists[0].filter((action) => action.bulk && lists.every((list) => list.some((a) => a.id === action.id)));
}

// What the toast says once a batch is back. `target` names who the faces
// went to, for an assign.
function faceBatchMessage(action, result, target) {
  const done = result.done.length;
  const skipped = result.skipped.length;
  let message = '';
  if (done) {
    message = action === 'assign' && target
      ? `${faceCount(done)} moved to ${target}`
      : `${faceCount(done)} ${FACE_DONE[action] || 'corrected'}`;
  }
  if (skipped) {
    const inPhoto = result.skipped.every((s) => s.reason === 'already_in_photo');
    const why = inPhoto ? ': that person is already in their photo' : '';
    const left = `${faceCount(skipped)} left as ${skipped === 1 ? 'it was' : 'they were'}${why}`;
    message = message ? `${message}. ${left}` : left;
  }
  return message;
}

// The uuids of `uuids` from `from` to `to` included, in page order; empty
// when either is missing.
function faceRange(uuids, from, to) {
  const start = uuids.indexOf(from);
  const end = uuids.indexOf(to);
  if (start < 0 || end < 0) return [];
  return uuids.slice(Math.min(start, end), Math.max(start, end) + 1);
}

// The photosApp() root reloads the page once it hears the event: an undo can
// put faces back anywhere, and the board that asked may be gone by then.
// Several tokens are undone last first, each batch having saved the faces as
// the one before left them.
async function undoFaceBatch(tokens) {
  try {
    let restored = 0;
    for (const token of tokens.slice().reverse()) {
      const result = await facesRequest(`${FACES_API}/faces/undo`, { method: 'POST', body: { token } });
      restored += result.restored;
    }
    window.AppAlert.success(`${faceCount(restored)} put back`, { duration: 2500 });
  } catch (err) {
    window.AppAlert.error(err.message || 'Could not undo');
  }
  window.dispatchEvent(new CustomEvent('photos-faces-changed'));
}

// Spread into a board component, whose init() calls initFaceSelection().
// The board defines facesSettled(done, action), which takes the corrected
// faces off its own lists.
window.faceSelectionMixin = function faceSelectionMixin() {
  return {
    selectedFaces: [],
    // null while the registry has not answered for the whole selection.
    faceActions: [],
    faceBusy: false,
    assignDialog: { query: '', results: [], clusters: [], loading: false },
    _faceActionsCache: {},
    _faceActionsGeneration: 0,
    _assignGeneration: 0,
    _faceAnchor: null,

    initFaceSelection() {
      this.$watch('selectedFaces', () => this._loadFaceActions());
    },

    // Every tile of the board, in page order.
    _faceUuids() {
      return Array.from(this.$root.querySelectorAll('[data-face-uuid]'), (el) => el.dataset.faceUuid);
    },

    isFaceSelected(uuid) {
      return this.selectedFaces.includes(uuid);
    },

    // Shift extends from the last face picked, as with photos.
    toggleFace(uuid, event) {
      const anchor = this._faceAnchor;
      if (event && event.shiftKey && anchor && anchor !== uuid && this.isFaceSelected(anchor)) {
        const range = faceRange(this._faceUuids(), anchor, uuid);
        if (range.length) {
          this.selectFaces(range);
          this._faceAnchor = uuid;
          return;
        }
      }
      if (this.isFaceSelected(uuid)) {
        this.selectedFaces = this.selectedFaces.filter((u) => u !== uuid);
        if (anchor === uuid) this._faceAnchor = null;
      } else {
        this.selectedFaces = this.selectedFaces.concat([uuid]);
        this._faceAnchor = uuid;
      }
    },

    selectFaces(uuids) {
      const added = uuids.filter((u) => !this.isFaceSelected(u));
      if (added.length) this.selectedFaces = this.selectedFaces.concat(added);
    },

    selectAllFaces() {
      this.selectFaces(this._faceUuids());
    },

    clearFaceSelection() {
      this.selectedFaces = [];
      this._faceAnchor = null;
    },

    // A face of a video opens it at the moment the face was seen.
    openFacePhoto(face) {
      openInViewer(face.file, face.file_name, face.file_type, face.timestamp);
    },

    // Asks the registry about the faces it has not answered for yet; the
    // generation drops an answer about a selection that changed since.
    async _loadFaceActions() {
      const uuids = this.selectedFaces.slice();
      const generation = ++this._faceActionsGeneration;
      const missing = uuids.filter((uuid) => !(uuid in this._faceActionsCache));
      if (missing.length) {
        this.faceActions = null;
        try {
          for (let i = 0; i < missing.length; i += FACE_ACTIONS_BATCH) {
            const data = await facesRequest(`${FACES_API}/faces/actions`, {
              method: 'POST',
              body: { uuids: missing.slice(i, i + FACE_ACTIONS_BATCH) },
            });
            Object.assign(this._faceActionsCache, data);
          }
        } catch (_) {
          if (generation === this._faceActionsGeneration) this.faceActions = [];
          return;
        }
      }
      if (generation !== this._faceActionsGeneration) return;
      this.faceActions = faceSelectionActions(uuids.map((uuid) => this._faceActionsCache[uuid] || []));
    },

    faceAllows(id) {
      return !this.faceBusy && (this.faceActions || []).some((a) => a.id === id);
    },

    runFaceAction(id) {
      if (!this.faceAllows(id)) return null;
      if (id === 'assign') return this.openAssignDialog();
      return this.applyFaceBatch({ action: id });
    },

    // Applies `body` to `uuids`, the selection by default. Returns the
    // batch's answer, or null when it failed.
    async applyFaceBatch(body, uuids = this.selectedFaces.slice(), target = null) {
      if (!uuids.length || this.faceBusy) return null;
      this.faceBusy = true;
      let result;
      try {
        result = await facesRequest(`${FACES_API}/faces/batch`, {
          method: 'POST',
          body: { ...body, faces: uuids },
        });
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not correct the faces');
        return null;
      } finally {
        this.faceBusy = false;
      }
      result.done.forEach((uuid) => { delete this._faceActionsCache[uuid]; });
      const done = new Set(result.done);
      this.selectedFaces = this.selectedFaces.filter((uuid) => !done.has(uuid));
      if (result.done.length) this.facesSettled(result.done, body.action);
      const message = faceBatchMessage(body.action, result, target);
      if (!result.done.length) {
        window.AppAlert.warning(message);
      } else {
        window.AppAlert.success(message, {
          duration: 8000,
          actions: result.undo ? [{ label: 'Undo', onClick: () => undoFaceBatch([result.undo]) }] : [],
        });
      }
      return result;
    },

    // ── "This is..." ─────────────────────────────────────

    async openAssignDialog() {
      this.assignDialog = { query: '', results: [], clusters: [], loading: true };
      document.getElementById('face-assign-dialog').showModal();
      try {
        const [results, clusters] = await Promise.all([
          facesRequest(personsUrl('')),
          facesRequest(`${FACES_API}/clusters`),
        ]);
        this.assignDialog.results = results;
        this.assignDialog.clusters = clusters.filter((c) => !c.person);
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not load people');
      } finally {
        this.assignDialog.loading = false;
      }
    },

    async searchAssign() {
      const generation = ++this._assignGeneration;
      try {
        const results = await facesRequest(personsUrl(this.assignDialog.query));
        if (generation === this._assignGeneration) this.assignDialog.results = results;
      } catch (_) {
        if (generation === this._assignGeneration) this.assignDialog.results = [];
      }
    },

    canCreateAssign() {
      const name = this.assignDialog.query.trim().toLowerCase();
      return !!name && !this.assignDialog.results.some((r) => r.name.toLowerCase() === name);
    },

    closeAssignDialog() {
      document.getElementById('face-assign-dialog').close();
    },

    _assign(body, target) {
      this.closeAssignDialog();
      return this.applyFaceBatch({ action: 'assign', ...body }, undefined, target);
    },

    assignToPerson(person) {
      return this._assign({ person: person.uuid }, person.name);
    },

    assignToNewPerson() {
      const name = this.assignDialog.query.trim();
      return name ? this._assign({ new_person: name }, name) : null;
    },

    assignToCluster(cluster) {
      return this._assign({ cluster: cluster.uuid }, 'an unnamed person');
    },

    assignToSomeoneNew() {
      return this._assign({ new_cluster: true }, 'someone new');
    },
  };
};

// The options of the naming list: the suggested person first while nothing
// is typed, the contacts found, then "Add ... to People" unless a contact
// has exactly the typed name.
function reviewOptions(query, results, suggestion) {
  const name = (query || '').trim();
  const options = [];
  if (!name && suggestion) options.push({ kind: 'person', person: suggestion, suggested: true });
  for (const person of results) {
    if (!name && suggestion && person.uuid === suggestion.uuid) continue;
    options.push({ kind: 'person', person, suggested: false });
  }
  if (name && !results.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
    options.push({ kind: 'create', name });
  }
  return options;
}

// `faces` without those in `done`, and how many are left of `total`.
function settleFaces(faces, total, done) {
  const gone = new Set(done);
  const kept = faces.filter((face) => !gone.has(face.uuid));
  return { faces: kept, total: total - (faces.length - kept.length) };
}

// A plain board: the faces embedded under `dataId` ({ faces, total }), each
// leaving once corrected, except by the actions in `stays`, which only
// change its assignment (a confirmed face stays on its person's page).
window.faceBoard = function faceBoard(dataId, { stays = [] } = {}) {
  return {
    ...window.faceSelectionMixin(),
    faces: [],
    total: 0,

    init() {
      const data = facesJson(dataId) || {};
      this.faces = data.faces || [];
      this.total = data.total || 0;
      this.initFaceSelection();
    },

    facesSettled(done, action) {
      if (stays.includes(action)) {
        const changed = new Set(done);
        this.faces = this.faces.map((face) => (changed.has(face.uuid) ? { ...face, assignment: 'confirmed' } : face));
        return;
      }
      const { faces, total } = settleFaces(this.faces, this.total, done);
      this.faces = faces;
      this.total = total;
      if (!faces.length && total > 0) {
        // The next faces were left out of the page.
        this.$ajax(window.location.pathname + window.location.search, {
          targets: ['photos-nav', 'photos-content'],
          focus: false,
        });
      }
    },
  };
};

// What answering a review card sends, in order. `kept` and `left` are the
// uuids of the card's faces kept and left out; `answer` is who the kept ones
// are ({ person } or { new_person }), or { hide: true } for nobody to name.
// The faces left out are taken out of whatever group the answer settles, and
// otherwise left as they were: either way they come back on a later card.
function reviewSteps(card, kept, left, answer) {
  const steps = [];
  if (card.kind === 'cluster') {
    // The whole cluster is named, its faces past the card's included.
    if (left.length) steps.push({ batch: { action: 'reject', faces: left } });
    if (answer.hide) steps.push({ cluster: { hidden: true } });
    else if (answer.person) steps.push({ cluster: { person: answer.person } });
    else steps.push({ cluster: { new_person: answer.new_person } });
    return steps;
  }
  if (answer.hide) {
    if (kept.length) steps.push({ batch: { action: 'hide', faces: kept } });
    return steps;
  }
  if (card.kind === 'check' && answer.person === card.guess.uuid) {
    if (kept.length) steps.push({ batch: { action: 'confirm', faces: kept } });
    if (left.length) steps.push({ batch: { action: 'reject', faces: left } });
    return steps;
  }
  if (kept.length) {
    const target = answer.person ? { person: answer.person } : { new_person: answer.new_person };
    steps.push({ batch: { action: 'assign', faces: kept, ...target } });
  }
  return steps;
}

// The review page: one card at a time, a group of faces and the question of
// who they are - an unnamed cluster, the faces the grouping put under a named
// person without being sure, or look-alike faces in no group. A click on a
// face leaves it out of the answer. Its own component, nested in
// photosApp(): a navigation away tears it down.
window.facesReview = function facesReview() {
  return {
    cards: [],
    left: 0,
    position: 0,
    excluded: [],
    query: '',
    results: [],
    active: 0,
    searching: false,
    saving: false,
    _searchGeneration: 0,

    init() {
      const data = facesJson('photos-review-data') || {};
      this.cards = data.cards || [];
      this.left = data.left || 0;
      if (this.cards.length) this.searchReviewNames();
    },

    current() {
      return this.cards[this.position] || null;
    },

    photoLabel(count) {
      return photoCount(count);
    },

    faceLabel(count) {
      return faceCount(count);
    },

    // ── The faces ───────────────────────────────────────

    isExcluded(uuid) {
      return this.excluded.includes(uuid);
    },

    toggleExcluded(uuid) {
      this.excluded = this.isExcluded(uuid)
        ? this.excluded.filter((u) => u !== uuid)
        : this.excluded.concat([uuid]);
    },

    keptCount() {
      const card = this.current();
      return card ? card.faces.length - this.excluded.length : 0;
    },

    // A face of a video opens it at the moment the face was seen.
    openFacePhoto(face) {
      openInViewer(face.file, face.file_name, face.file_type, face.timestamp);
    },

    // ── What the card says ──────────────────────────────

    question() {
      const card = this.current();
      return card && card.kind === 'check' ? `Is this ${card.guess.name}?` : 'Who is this?';
    },

    subtitle() {
      const card = this.current();
      if (!card) return '';
      if (card.kind === 'cluster') return `${photoCount(card.photo_count)}, grouped by likeness`;
      if (card.kind === 'check') return `${faceCount(card.total)} put with them without being sure`;
      return card.total === 1 ? '1 face in no group' : `${card.total} look-alike faces in no group`;
    },

    guessLabel() {
      const card = this.current();
      const option = this.guessOption();
      if (!card || !option) return '';
      if (card.kind !== 'check') return `It's ${option.person.name}`;
      return this.keptCount() ? `Yes, it's ${option.person.name}` : `None of them is ${option.person.name}`;
    },

    guessHint() {
      const card = this.current();
      if (!card) return '';
      if (card.kind !== 'check') return 'Looks like them';
      const kept = this.keptCount();
      if (!kept) return 'Takes them out of the group';
      return this.excluded.length ? `Confirms ${faceCount(kept)}, takes ${this.excluded.length} out` : `Confirms ${faceCount(kept)}`;
    },

    // ── The answer ──────────────────────────────────────

    options() {
      const card = this.current();
      return reviewOptions(this.query, this.results, card ? card.guess : null);
    },

    // The guess, offered as the answer at hand while nothing is typed.
    guessOption() {
      const first = this.options()[0];
      return first && first.suggested ? first : null;
    },

    otherOptions() {
      return this.guessOption() ? this.options().slice(1) : this.options();
    },

    // Every face left out of a person's doubtful ones is still an answer
    // about them: none of these is them.
    guessRejectsAll() {
      const card = this.current();
      return !!card && card.kind === 'check' && this.keptCount() === 0;
    },

    canAnswer() {
      return !this.saving && (this.keptCount() > 0 || this.guessRejectsAll());
    },

    canPick(option) {
      return this.canAnswer() && (option.suggested || !this.guessRejectsAll());
    },

    async searchReviewNames() {
      const generation = ++this._searchGeneration;
      this.searching = true;
      try {
        const results = await facesRequest(personsUrl(this.query));
        if (generation === this._searchGeneration) {
          this.results = results;
          this.active = 0;
        }
      } catch (_) {
        if (generation === this._searchGeneration) this.results = [];
      } finally {
        if (generation === this._searchGeneration) this.searching = false;
      }
    },

    moveActive(step) {
      const count = this.options().length;
      if (count) this.active = (this.active + step + count) % count;
    },

    pickActive() {
      const option = this.options()[this.active];
      if (option) this.pick(option);
    },

    pick(option) {
      if (!this.canPick(option)) return null;
      const answer = option.kind === 'create'
        ? { new_person: option.name, name: option.name }
        : { person: option.person.uuid, name: option.person.name };
      return this._settle(answer);
    },

    hideCurrent() {
      if (!this.current() || !this.keptCount()) return null;
      return this._settle({ hide: true });
    },

    // Sends the answer; true once the card is settled and left the queue.
    async _settle(answer) {
      const card = this.current();
      if (!card || this.saving) return false;
      const faces = card.faces.map((face) => face.uuid);
      const left = faces.filter((uuid) => this.isExcluded(uuid));
      const kept = faces.filter((uuid) => !this.isExcluded(uuid));
      const undo = [];
      let main = null;
      let sent = 0;
      this.saving = true;
      try {
        for (const step of reviewSteps(card, kept, left, answer)) {
          if (step.batch) {
            const result = await facesRequest(`${FACES_API}/faces/batch`, { method: 'POST', body: step.batch });
            if (result.undo) undo.push(result.undo);
            if (!main) main = { action: step.batch.action, result };
          } else {
            await facesRequest(`${FACES_API}/clusters/${card.cluster}`, { method: 'PATCH', body: step.cluster });
          }
          sent += 1;
        }
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not save');
        // What went through changed the card: start again from the server.
        if (sent) this._reloadQueue();
        return false;
      } finally {
        this.saving = false;
      }
      this._announce(card, answer, main, undo);
      this.cards.splice(this.position, 1);
      this.left = Math.max(0, this.left - 1);
      if (!this.cards.length) {
        // What was left out, and the cards past this page, come now.
        this._reloadQueue();
        return true;
      }
      if (this.position >= this.cards.length) this.position = 0;
      this._nextCard();
      return true;
    },

    _announce(card, answer, main, undo) {
      if (card.kind === 'cluster') {
        window.AppAlert.success(answer.hide ? 'Hidden' : `Named ${answer.name}`, { duration: 1500 });
        return;
      }
      if (!main) return;
      const message = faceBatchMessage(main.action, main.result, answer.name);
      if (!main.result.done.length) {
        window.AppAlert.warning(message);
        return;
      }
      window.AppAlert.success(message, {
        duration: 8000,
        actions: undo.length ? [{ label: 'Undo', onClick: () => undoFaceBatch(undo) }] : [],
      });
    },

    skip() {
      if (this.cards.length < 2) return;
      this.position = (this.position + 1) % this.cards.length;
      this._nextCard();
    },

    previous() {
      if (this.cards.length < 2) return;
      this.position = (this.position - 1 + this.cards.length) % this.cards.length;
      this._nextCard();
    },

    _nextCard() {
      this.excluded = [];
      this.query = '';
      this.active = 0;
      if (this.cards.length) this.searchReviewNames();
      this.$nextTick(() => {
        if (this.$refs.reviewInput) this.$refs.reviewInput.focus();
      });
    },

    _reloadQueue() {
      this.$ajax(window.location.pathname + window.location.search, {
        targets: ['photos-nav', 'photos-content'],
        focus: false,
      });
    },
  };
};
