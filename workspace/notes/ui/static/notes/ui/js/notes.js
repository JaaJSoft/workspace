// ── Notes Preferences ────────────────────────────────────
window._notesPrefsDefaults = {
    showTags: true,
    showFolders: true,
    showJournal: true,
    defaultView: 'all',
    sortBy: 'modified',
    confirmBeforeDelete: true,
    hiddenItems: [],
    showHidden: false,
};
window._notesPrefsCache = { ...window._notesPrefsDefaults };

// Eagerly fetch prefs so they're ready before Alpine init
window._notesPrefsReady = fetch('/api/v1/settings/notes/preferences', { credentials: 'same-origin' })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
        if (data && data.value && typeof data.value === 'object') {
            window._notesPrefsCache = { ...window._notesPrefsDefaults, ...data.value };
        }
    })
    .catch(function() {});

window.notesPreferences = function notesPreferences() {
    var API_URL = '/api/v1/settings/notes/preferences';
    var _saveTimer = null;

    return {
        prefs: { ...window._notesPrefsCache },

        async init() {
            await window._notesPrefsReady;
            this.prefs = { ...window._notesPrefsCache };
            // Sync when prefs change from other components
            window.addEventListener('notes:preferences-changed', function(e) {
                this.prefs = { ...e.detail };
            }.bind(this));
        },

        update(key, value) {
            this.prefs[key] = value;
            this._saveRemote();
            this._broadcast();
        },

        _broadcast() {
            window._notesPrefsCache = { ...this.prefs };
            window.dispatchEvent(new CustomEvent('notes:preferences-changed', { detail: this.prefs }));
        },

        _saveRemote() {
            clearTimeout(_saveTimer);
            _saveTimer = setTimeout(function() {
                fetch(API_URL, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                    body: JSON.stringify({ value: this.prefs }),
                }).catch(function() {});
            }.bind(this), 500);
        },
    };
};

// ── Notes App ────────────────────────────────────────────
window.notesApp = function notesApp(config) {
    config = config || {};
    var prefs = window._notesPrefsCache;
    var initialView = config.view || prefs.defaultView || 'all';
    var titleMap = { all: 'All notes', recent: 'Recent', journal: 'Journal' };

    return {
        // Sidebar
        collapsed: false,
        activeView: initialView,
        activeId: config.id || null,
        viewTitle: titleMap[initialView] || 'All notes',

        // Preferences (reactive copy)
        notePrefs: { ...window._notesPrefsCache },

        // Context menu
        ctxMenu: { open: false, x: 0, y: 0, type: null, data: null },

        // Note list
        notes: [],
        loadingNotes: false,

        // Tags (from shared mixin)
        ...window.tagsMixin(),

        // Editor
        selectedNote: null,
        loadingEditor: false,
        _loadedScripts: [],
        _loadGeneration: 0,

        async init() {
            this.collapsed = localStorage.getItem('notes-sidebar-collapsed') === 'true';

            // Wait for preferences to be loaded
            await window._notesPrefsReady;
            this.notePrefs = { ...window._notesPrefsCache };

            // Resolve initial view: URL param takes priority, then saved pref
            var savedPrefs = window._notesPrefsCache;
            if (!config.view && savedPrefs.defaultView && savedPrefs.defaultView !== 'all') {
                initialView = savedPrefs.defaultView;
                this.activeView = initialView;
                this.viewTitle = titleMap[initialView] || 'All notes';
            }

            // Listen for sidebar refresh events
            window.addEventListener('notes:refresh-sidebar', this.refreshSidebar.bind(this));

            // Listen for file action dialog events (use named functions to prevent duplicates)
            var self = this;
            if (!window._notesFileActionsRegistered) {
                window._notesFileActionsRegistered = true;
                window.addEventListener('create-folder', function(e) {
                    window.fileActions.createFolder(e.detail.name, null)
                        .then(function() { self.refreshSidebar(); })
                        .catch(function() {});
                });
                window.addEventListener('rename-item', function(e) {
                    window.fileActions.renameItem(e.detail.uuid, e.detail.name)
                        .then(function() { self.refreshSidebar(); })
                        .catch(function() {});
                });
            }

            // Sync reactive prefs and re-sort when preferences change
            window.addEventListener('notes:preferences-changed', function(e) {
                this.notePrefs = { ...e.detail };
                if (this.activeView && this.activeView !== 'journal') {
                    this.setView(this.activeView, this.activeId, this.viewTitle, true);
                }
            }.bind(this));

            // Load tags for the editor dropdown
            await this.loadTags();

            // Load initial notes based on SSR state
            if (initialView === 'journal') {
                await this.openJournal();
            } else {
                await this.setView(initialView, config.id, null, true);
            }

            // Auto-open note if specified
            if (config.file) {
                var note = this.notes.find(function(n) { return n.uuid === config.file; });
                if (note) {
                    await this.selectNote(note);
                } else {
                    await this.selectNoteById(config.file);
                }
            }
        },

        // ── Sidebar navigation ──────────────────────────────

        _sortParam() {
            var sortMap = { name: 'name', modified: '-updated_at', created: '-created_at' };
            return sortMap[window._notesPrefsCache.sortBy] || '-updated_at';
        },

        async setView(view, id, name, skipUrl) {
            this.activeView = view;
            this.activeId = id || null;

            var sort = '&ordering=' + this._sortParam();
            var url;
            if (view === 'all') {
                this.viewTitle = 'All notes';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200' + sort;
            } else if (view === 'recent') {
                this.viewTitle = 'Recent';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=50' + sort;
            } else if (view === 'tag') {
                if (!name && id) {
                    var tagEl = document.querySelector('[data-tag-uuid="' + id + '"]');
                    if (tagEl) name = tagEl.dataset.tagName;
                }
                this.viewTitle = name || 'Tag';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200&tags=' + id + sort;
            } else if (view === 'folder') {
                if (!name && id) {
                    var folderEl = document.querySelector('[data-folder-uuid="' + id + '"]');
                    if (folderEl) name = folderEl.dataset.folderName;
                }
                this.viewTitle = name || 'Folder';
                url = '/api/v1/files?mime_type=text/markdown&parent=' + id + sort;
            } else {
                this.viewTitle = 'All notes';
                this.activeView = 'all';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200' + sort;
            }

            await this.loadNotes(url);

            if (!skipUrl) {
                this.selectedNote = null;
                this.updateUrl();
            }
        },

        async loadNotes(url) {
            this.notes = [];
            this.loadingNotes = true;
            var resp = await fetch(url);
            if (resp.ok) {
                this.notes = await resp.json();
            }
            this.loadingNotes = false;
        },

        // ── Journal ─────────────────────────────────────────

        async openJournal() {
            this.activeView = 'journal';
            this.activeId = null;
            this.viewTitle = 'Journal';

            // Find or create the Journal folder
            var resp = await fetch('/api/v1/files?node_type=folder&parent=&search=Journal');
            var folders = [];
            if (resp.ok) {
                folders = await resp.json();
            }

            var journalFolder = folders.find(function(f) { return f.name === 'Journal' && !f.parent; });

            if (!journalFolder) {
                var createResp = await fetch('/api/v1/files', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ name: 'Journal', node_type: 'folder', icon: 'book-open', color: 'success' }),
                });
                if (createResp.ok) {
                    journalFolder = await createResp.json();
                    window.dispatchEvent(new CustomEvent('notes:refresh-sidebar'));
                } else {
                    return;
                }
            }

            this.activeId = journalFolder.uuid;
            await this.loadNotes('/api/v1/files?mime_type=text/markdown&parent=' + journalFolder.uuid + '&ordering=-name');

            // Create today's note if needed
            var today = new Date().toISOString().split('T')[0];
            var todayName = today + '.md';
            var todayNote = this.notes.find(function(n) { return n.name === todayName; });

            if (!todayNote) {
                todayNote = await this._createMdFile(todayName, journalFolder.uuid);
                if (todayNote) {
                    this.notes.unshift(todayNote);
                }
            }

            if (todayNote) {
                await this.selectNote(todayNote);
            }

            this.updateUrl();
        },

        // ── Note selection ──────────────────────────────────

        async selectNote(note) {
            this.selectedNote = note;
            this.updateUrl();
            await this.$nextTick();
            await this.loadViewer(note);
        },

        async selectNoteById(uuid) {
            var resp = await fetch('/api/v1/files/' + uuid);
            if (resp.ok) {
                var note = await resp.json();
                await this.selectNote(note);
            }
        },

        async loadViewer(note) {
            var container = this.$refs.editorContainer;
            if (!container) return;

            // Cleanup previous viewer
            window.dispatchEvent(new CustomEvent('viewer-cleanup'));
            container.replaceChildren();
            this._loadedScripts.forEach(function(s) { s.remove(); });
            this._loadedScripts = [];

            var generation = ++this._loadGeneration;
            this.loadingEditor = true;

            try {
                var resp = await fetch('/files/view/' + note.uuid);
                if (generation !== this._loadGeneration) return;
                if (!resp.ok) throw new Error('Failed to load viewer: ' + resp.status);

                var rawHtml = await resp.text();
                if (generation !== this._loadGeneration) return;

                var temp = document.createElement('template');
                temp.innerHTML = rawHtml;

                var scriptEls = temp.content.querySelectorAll('script');
                var scripts = [];
                scriptEls.forEach(function(el) {
                    scripts.push(el.textContent);
                    el.remove();
                });

                scripts.forEach(function(scriptContent) {
                    var newScript = document.createElement('script');
                    newScript.textContent = scriptContent;
                    document.head.appendChild(newScript);
                    this._loadedScripts.push(newScript);
                }.bind(this));

                while (temp.content.firstChild) {
                    container.appendChild(temp.content.firstChild);
                }
            } catch (err) {
                if (generation !== this._loadGeneration) return;
                container.innerHTML = '<div class="flex items-center justify-center h-full text-error"><p>' + err.message + '</p></div>';
            } finally {
                if (generation === this._loadGeneration) {
                    this.loadingEditor = false;
                }
            }
        },

        // ── Note CRUD ───────────────────────────────────────

        async createNote() {
            var name = prompt('Note name:');
            if (!name) return;
            if (!name.endsWith('.md')) name += '.md';

            var parentUuid = (this.activeView === 'folder' || this.activeView === 'journal') ? this.activeId : null;
            var note = await this._createMdFile(name, parentUuid);
            if (note) {
                this.notes.unshift(note);
                await this.selectNote(note);
            }
        },

        async renameNote(newName) {
            if (!this.selectedNote || !newName) return;
            if (!newName.endsWith('.md')) newName += '.md';
            if (newName === this.selectedNote.name) return;

            var resp = await fetch('/api/v1/files/' + this.selectedNote.uuid, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ name: newName }),
            });

            if (resp.ok) {
                this.selectedNote.name = newName;
            }
        },

        async deleteNote() {
            if (!this.selectedNote) return;
            if (window._notesPrefsCache.confirmBeforeDelete) {
                if (!confirm('Delete "' + this.noteName(this.selectedNote) + '"?')) return;
            }

            window.dispatchEvent(new CustomEvent('viewer-cleanup'));

            var resp = await fetch('/api/v1/files/' + this.selectedNote.uuid, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });

            if (resp.ok) {
                var uuid = this.selectedNote.uuid;
                this.notes = this.notes.filter(function(n) { return n.uuid !== uuid; });
                var container = this.$refs.editorContainer;
                if (container) container.replaceChildren();
                this._loadedScripts.forEach(function(s) { s.remove(); });
                this._loadedScripts = [];
                this.selectedNote = null;
                this.updateUrl();
            }
        },

        // ── File actions (delegate to shared helpers) ───────

        showCreateFolderDialog: function() {
            window.fileActions.showCreateFolderDialog();
        },

        showRenameDialog: function(uuid, name) {
            window.fileActions.showRenameDialog(uuid, name);
        },

        // ── Context menu ─────────────────────────────────────

        openCtxMenu(e, type, data) {
            e.preventDefault();
            var x = e.clientX;
            var y = e.clientY;
            // Prevent overflow
            var menuW = 200, menuH = 160;
            if (x + menuW > window.innerWidth) x = window.innerWidth - menuW;
            if (y + menuH > window.innerHeight) y = window.innerHeight - menuH;
            this.ctxMenu = { open: true, x: x, y: y, type: type, data: data };
        },

        closeCtxMenu() {
            this.ctxMenu.open = false;
        },

        ctxAction(action) {
            var m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            if (m.type === 'folder') {
                if (action === 'rename') {
                    this.showRenameDialog(m.data.uuid, m.data.name);
                } else if (action === 'delete') {
                    if (!confirm('Delete folder "' + m.data.name + '"? Notes inside will be moved to trash.')) return;
                    var self = this;
                    fetch('/api/v1/files/' + m.data.uuid, {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCSRFToken() },
                    }).then(function(resp) {
                        if (resp.ok) self.refreshSidebar();
                    });
                }
            } else if (m.type === 'tag') {
                if (action === 'edit') {
                    this.showTagModal(m.data);
                } else if (action === 'delete') {
                    if (!confirm('Delete tag "' + m.data.name + '"?')) return;
                    var self = this;
                    fetch('/api/v1/tags/' + m.data.uuid, {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCSRFToken() },
                    }).then(function(resp) {
                        if (resp.ok || resp.status === 204) {
                            self.allTags = self.allTags.filter(function(t) { return t.uuid !== m.data.uuid; });
                            self.refreshSidebar();
                        }
                    });
                }
            }

            if (action === 'hide') {
                this.toggleHidden(m.data.uuid);
            }
        },

        isHidden(uuid) {
            return (this.notePrefs.hiddenItems || []).indexOf(uuid) !== -1;
        },

        toggleHidden(uuid) {
            var list = (this.notePrefs.hiddenItems || []).slice();
            var idx = list.indexOf(uuid);
            if (idx === -1) {
                list.push(uuid);
            } else {
                list.splice(idx, 1);
            }
            this._updatePref('hiddenItems', list);
        },

        _updatePref(key, value) {
            this.notePrefs[key] = value;
            window._notesPrefsCache[key] = value;
            // Notify other components (e.g. preferences dropdown)
            window.dispatchEvent(new CustomEvent('notes:preferences-changed', {
                detail: { ...window._notesPrefsCache }
            }));
            // Persist to server
            var prefs = { ...window._notesPrefsCache };
            fetch('/api/v1/settings/notes/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ value: prefs }),
            }).catch(function() {});
        },

        // ── URL state ────────────────────────────────────────

        updateUrl() {
            var url = new URL(window.location);
            url.search = '';

            if (this.activeView !== 'all') {
                url.searchParams.set('view', this.activeView);
            }
            if (this.activeView === 'tag' && this.activeId) {
                url.searchParams.set('tag', this.activeId);
            }
            if (this.activeView === 'folder' && this.activeId) {
                url.searchParams.set('folder', this.activeId);
            }
            if (this.selectedNote) {
                url.searchParams.set('file', this.selectedNote.uuid);
            }

            window.history.replaceState({}, '', url);
        },

        // ── Sidebar refresh ──────────────────────────────────

        async refreshSidebar() {
            var resp = await fetch('/notes', {
                headers: { 'X-Alpine-Request': 'true' },
            });
            if (resp.ok) {
                var html = await resp.text();
                var container = document.getElementById('notes-sidebar');
                if (container) {
                    container.innerHTML = html;
                    // Re-init Lucide icons in the new HTML
                    if (window.lucide) window.lucide.createIcons();
                }
            }
        },

        // ── Helpers ─────────────────────────────────────────

        async _createMdFile(name, parentUuid) {
            var formData = new FormData();
            formData.append('name', name);
            formData.append('node_type', 'file');
            formData.append('mime_type', 'text/markdown');
            formData.append('content', new Blob([''], { type: 'text/markdown' }), name);
            if (parentUuid) {
                formData.append('parent', parentUuid);
            }

            var resp = await fetch('/api/v1/files', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
                body: formData,
            });

            if (resp.ok) {
                var note = await resp.json();
                note.tags = [];
                return note;
            }
            return null;
        },

        noteName(note) {
            return note.name.replace(/\.md$/i, '');
        },

        formatDate(dateStr) {
            if (!dateStr) return '';
            var d = new Date(dateStr);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },

        toggleCollapse() {
            this.collapsed = !this.collapsed;
            localStorage.setItem('notes-sidebar-collapsed', this.collapsed);
        },

        isMobile() {
            return window.innerWidth < 1024;
        },

        destroy() {
            window.dispatchEvent(new CustomEvent('viewer-cleanup'));
            this._loadedScripts.forEach(function(s) { s.remove(); });
            this._loadedScripts = [];
        },
    };
};
