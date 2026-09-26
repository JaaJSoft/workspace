// Photos: face grouping - the People tab, a person's page, the "People in
// this photo" and merge dialogs. Spread into photosApp() (photos.js), so it
// holds methods only: a getter would be frozen by the spread.

const FACES_API = '/api/v1/photos';
const CLUSTER_MENU_WIDTH = 224;
const CLUSTER_MENU_HEIGHT = 180;
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
    let detail = '';
    try {
      const data = await response.json();
      detail = data.detail || Object.values(data).flat().join(' ');
    } catch (_) {
      // Not JSON: the status says enough.
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

window.photosFacesMixin = function photosFacesMixin() {
  return {
    facesSaving: false,
    clusterMenu: { open: false, x: 0, y: 0, cluster: null },
    facesDialog: {
      photo: null, faces: [], clusters: [], loading: false, busy: false, picking: null, changed: false,
    },
    mergeDialog: { target: null, clusters: [], selected: [], loading: false, saving: false },

    // Both read from the page each time: a sidebar navigation swaps the
    // content they are embedded in.
    facesEnabled() {
      return facesJson('photos-faces-enabled') === true;
    },

    currentCluster() {
      return facesJson('photos-cluster-data');
    },

    _reloadView(url = window.location.href) {
      return this.$ajax(url, { targets: ['photos-nav', 'photos-content'], focus: false });
    },

    _peopleUrl() {
      return '/photos/people';
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
        message: 'Every face, every group of people and every face picture is deleted right away. Your photos are not touched. Turning it back on reads your photos again from the start.',
        okLabel: 'Turn off and delete',
        okClass: 'btn-error',
        icon: 'power-off',
        iconClass: 'bg-error/10 text-error',
      });
      if (ok) await this.setFacesEnabled(false);
    },

    // ── Cluster menu and actions ────────────────────────

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
      this.clusterMenu = {
        open: true,
        x,
        y,
        cluster: {
          uuid: card.dataset.cluster,
          hidden: card.dataset.hidden === '1',
          cover_url: cover ? cover.getAttribute('src') : null,
        },
      };
    },

    closeClusterMenu() {
      this.clusterMenu.open = false;
    },

    async runClusterAction(action, cluster) {
      this.closeClusterMenu();
      if (document.activeElement) document.activeElement.blur();
      if (!cluster) return;
      const url = `${FACES_API}/clusters/${cluster.uuid}`;
      const onItsPage = this.currentCluster()?.uuid === cluster.uuid;
      try {
        switch (action) {
          case 'hide':
          case 'show':
            await facesRequest(url, { method: 'PATCH', body: { hidden: action === 'hide' } });
            await this._reloadView();
            break;
          case 'merge':
            await this.openMergeDialog(cluster);
            break;
          case 'ungroup': {
            const ok = await AppDialog.confirm({
              title: 'Ungroup',
              message: 'This person leaves the list, and their faces stay out of automatic grouping. You can still put a face back with "People in this photo".',
              okLabel: 'Ungroup',
              okClass: 'btn-error',
              icon: 'ungroup',
              iconClass: 'bg-error/10 text-error',
            });
            if (!ok) return;
            await facesRequest(url, { method: 'DELETE' });
            await this._reloadView(onItsPage ? this._peopleUrl() : window.location.href);
            break;
          }
        }
      } catch (err) {
        window.AppAlert.error(err.message || 'Something went wrong');
      }
    },

    // ── Merge dialog ────────────────────────────────────

    async openMergeDialog(cluster) {
      this.mergeDialog = { target: cluster, clusters: [], selected: [], loading: true, saving: false };
      document.getElementById('merge-clusters-dialog').showModal();
      try {
        const clusters = await facesRequest(`${FACES_API}/clusters`);
        const target = clusters.find((c) => c.uuid === cluster.uuid);
        if (target) this.mergeDialog.target = target;
        this.mergeDialog.clusters = clusters.filter((c) => c.uuid !== cluster.uuid);
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
    },

    closeMergeDialog() {
      document.getElementById('merge-clusters-dialog').close();
    },

    async submitMerge() {
      const target = this.mergeDialog.target;
      if (!target || !this.mergeDialog.selected.length) return;
      this.mergeDialog.saving = true;
      try {
        await facesRequest(`${FACES_API}/clusters/${target.uuid}/merge`, {
          method: 'POST',
          body: { clusters: this.mergeDialog.selected },
        });
        this.closeMergeDialog();
        await this._reloadView();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not merge');
      } finally {
        this.mergeDialog.saving = false;
      }
    },

    // ── "People in this photo" ──────────────────────────

    async openFacesDialog(photo) {
      this.closeCtxMenu();
      this.facesDialog = {
        photo, faces: [], clusters: [], loading: true, busy: false, picking: null, changed: false,
      };
      document.getElementById('photo-faces-dialog').showModal();
      try {
        const [faces, clusters] = await Promise.all([
          facesRequest(`${FACES_API}/files/${photo.uuid}/faces`),
          facesRequest(`${FACES_API}/clusters`),
        ]);
        this.facesDialog.faces = faces;
        this.facesDialog.clusters = clusters;
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not load the faces');
      } finally {
        this.facesDialog.loading = false;
      }
    },

    clusterOf(face) {
      return this.facesDialog.clusters.find((c) => c.uuid === face.cluster) || null;
    },

    faceLabel(face) {
      const cluster = this.clusterOf(face);
      if (cluster) return `${cluster.photo_count} photo${cluster.photo_count === 1 ? '' : 's'}`;
      if (face.cluster) return 'Hidden person';
      if (face.assignment === 'rejected') return 'Left out of grouping';
      return 'Not grouped yet';
    },

    // Someone else than the face's own cluster, and never a cluster that
    // already holds another face of this photo: the server refuses those.
    pickableClusters(face) {
      const taken = new Set(
        this.facesDialog.faces.filter((f) => f.uuid !== face.uuid && f.cluster).map((f) => f.cluster),
      );
      return this.facesDialog.clusters.filter((c) => c.uuid !== face.cluster && !taken.has(c.uuid));
    },

    async _correctFace(face, body) {
      this.facesDialog.busy = true;
      try {
        const updated = await facesRequest(`${FACES_API}/faces/${face.uuid}`, { method: 'PATCH', body });
        this.facesDialog.faces = this.facesDialog.faces.map((f) => (f.uuid === face.uuid ? updated : f));
        this.facesDialog.picking = null;
        this.facesDialog.changed = true;
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not correct the face');
      } finally {
        this.facesDialog.busy = false;
      }
    },

    confirmFace(face) {
      return this._correctFace(face, { assignment: 'confirmed' });
    },

    rejectFace(face) {
      return this._correctFace(face, { cluster: null });
    },

    assignFace(face, cluster) {
      return this._correctFace(face, { cluster: cluster.uuid });
    },

    // Someone who has no cluster yet: the face starts one, confirmed.
    async startCluster(face) {
      this.facesDialog.busy = true;
      try {
        const cluster = await facesRequest(`${FACES_API}/clusters`, {
          method: 'POST',
          body: { face: face.uuid },
        });
        this.facesDialog.clusters = [...this.facesDialog.clusters, cluster];
        this.facesDialog.faces = this.facesDialog.faces.map((f) => (
          f.uuid === face.uuid ? { ...f, cluster: cluster.uuid, assignment: 'confirmed' } : f
        ));
        this.facesDialog.picking = null;
        this.facesDialog.changed = true;
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not create the person');
      } finally {
        this.facesDialog.busy = false;
      }
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
      const cluster = this.currentCluster();
      if (!cluster) return null;
      const faces = await facesRequest(`${FACES_API}/files/${photo.uuid}/faces`);
      return faces.find((f) => f.cluster === cluster.uuid) || null;
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
        await this._reloadView();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not change the cover');
      }
    },

    async rejectFromCluster(photo) {
      this.closeCtxMenu();
      try {
        const face = await this._faceInCurrentCluster(photo);
        if (!face) return;
        await facesRequest(`${FACES_API}/faces/${face.uuid}`, { method: 'PATCH', body: { cluster: null } });
        await this._reloadView();
      } catch (err) {
        window.AppAlert.error(err.message || 'Could not remove the photo');
      }
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
      this.analyzed = status.analyzed;
      if (status.analyzed >= status.total) {
        clearInterval(this._timer);
        this.$ajax(window.location.href, { targets: ['photos-nav', 'photos-content'], focus: false });
      }
    },
  };
};
