// ── Notes Preferences ────────────────────────────────────
window._notesPrefsDefaults = {
    showTags: true,
    showFolders: true,
    showJournal: true,
    showGroupFolders: true,
    defaultView: 'all',
    sortBy: 'modified',
    confirmBeforeDelete: true,
    hiddenItems: [],
    showHidden: false,
    defaultFolderUuid: null,
    journalFolderUuid: null,
    // expandedFolders removed — now stored in URL
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
    const API_URL = '/api/v1/settings/notes/preferences';
    let _saveTimer = null;

    return {
        prefs: { ...window._notesPrefsCache },
        defaultFolderName: '',
        journalFolderName: '',

        async init() {
            await window._notesPrefsReady;
            this.prefs = { ...window._notesPrefsCache };
            window.addEventListener('notes:preferences-changed', function(e) {
                this.prefs = { ...e.detail };
                this._loadFolderNames();
            }.bind(this));
            // Load folder names for display
            await this._loadFolderNames();
        },

        update(key, value) {
            this.prefs[key] = value;
            this._saveRemote();
            this._broadcast();
        },

        async pickDefaultFolder() {
            const folder = await AppDialog.folderPicker({
                title: 'Default notes folder',
                message: 'Choose where new notes are created by default.',
                okLabel: 'Select',
                okClass: 'btn-success',
                icon: 'folder-pen',
                iconClass: 'bg-success/10 text-success',
            });
            if (!folder) return;
            this.prefs.defaultFolderUuid = folder.uuid;
            this.defaultFolderName = folder.name || 'Root';
            if (folder.uuid) {
                try {
                    const r = await fetch('/api/v1/files/' + folder.uuid);
                    if (r.ok) { const f = await r.json(); this.defaultFolderName = f.path || f.name; }
                } catch(e) {}
            }
            this._saveRemote();
            this._broadcast();
        },

        async pickJournalFolder() {
            const folder = await AppDialog.folderPicker({
                title: 'Journal folder',
                message: 'Choose which folder to use for daily journal notes.',
                okLabel: 'Select',
                okClass: 'btn-success',
                icon: 'book-open',
                iconClass: 'bg-success/10 text-success',
            });
            if (!folder) return;
            this.prefs.journalFolderUuid = folder.uuid;
            this.journalFolderName = folder.name || 'Root';
            if (folder.uuid) {
                try {
                    const r2 = await fetch('/api/v1/files/' + folder.uuid);
                    if (r2.ok) { const f2 = await r2.json(); this.journalFolderName = f2.path || f2.name; }
                } catch(e) {}
            }
            this._saveRemote();
            this._broadcast();
        },

        async _loadFolderNames() {
            if (this.prefs.defaultFolderUuid) {
                try {
                    const r = await fetch('/api/v1/files/' + this.prefs.defaultFolderUuid);
                    if (r.ok) { const f = await r.json(); this.defaultFolderName = f.path || f.name; }
                    else this.defaultFolderName = 'Not set';
                } catch(e) { this.defaultFolderName = 'Not set'; }
            } else {
                this.defaultFolderName = 'Not set';
            }

            if (this.prefs.journalFolderUuid) {
                try {
                    const r2 = await fetch('/api/v1/files/' + this.prefs.journalFolderUuid);
                    if (r2.ok) { const f2 = await r2.json(); this.journalFolderName = f2.path || f2.name; }
                    else this.journalFolderName = 'Not set';
                } catch(e) { this.journalFolderName = 'Not set'; }
            } else {
                this.journalFolderName = 'Not set';
            }
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
    const prefs = window._notesPrefsCache;
    const initialView = config.view || prefs.defaultView || 'all';
    const titleMap = { all: 'All notes', favorites: 'Favorites', recent: 'Recent', journal: 'Journal' };

    return {
        // Sidebar
        collapsed: false,
        activeView: initialView,
        activeId: config.id || null,
        viewTitle: titleMap[initialView] || 'All notes',

        // Folder arrays (flat lists, lazy-loaded children)
        sidebarFolders: [],
        sidebarGroupFolders: [],
        _loadedChildren: {},    // uuid -> true if children have been fetched
        loadingChildren: [],   // uuids currently loading (reactive array)

        // Preferences (reactive copy)
        notePrefs: { ...window._notesPrefsCache },

        // Context menu
        ctxMenu: { open: false, x: 0, y: 0, type: null, data: null, actions: null },

        // Note list
        notes: [],
        loadingNotes: false,
        togglingFavorite: false,

        // Filters
        filters: {
            search: '',
            favorites: false,
            tags: [],
        },
        _searchTimer: null,
        showTagDropdown: false,

        // Tags (from shared mixin)
        ...window.tagsMixin(),

        // Editor
        selectedNote: null,
        loadingEditor: false,
        _loadedScripts: [],
        _loadGeneration: 0,

        // Available actions on the currently selected note — driven by
        // POST /api/v1/files/actions. Empty list = fail-safe (buttons disabled).
        selectedNoteActionIds: [],
        _actionsFetchGen: 0,

        async init() {
            this.collapsed = localStorage.getItem('notes-sidebar-collapsed') === 'true';

            // Load folder data from embedded JSON
            this._loadFolderData();

            // Wait for preferences to be loaded
            await window._notesPrefsReady;
            this.notePrefs = { ...window._notesPrefsCache };

            // Resolve initial view: URL param takes priority, then saved pref
            const savedPrefs = window._notesPrefsCache;
            if (!config.view && savedPrefs.defaultView && savedPrefs.defaultView !== 'all') {
                initialView = savedPrefs.defaultView;
                this.activeView = initialView;
                this.viewTitle = titleMap[initialView] || 'All notes';
            }

            // Listen for sidebar refresh events
            window.addEventListener('notes:refresh-sidebar', this.refreshSidebar.bind(this));

            // Listen for file action dialog events (use named functions to prevent duplicates)
            const self = this;
            if (!window._notesFileActionsRegistered) {
                window._notesFileActionsRegistered = true;
                window.addEventListener('create-folder', function(e) {
                    window.fileActions.createFolder(e.detail.name, null)
                        .then(function() { self.refreshSidebar(); })
                        .catch(function() {});
                });
                window.addEventListener('rename-item', function(e) {
                    window.fileActions.renameItem(e.detail.uuid, e.detail.name)
                        .then(function() {
                            self.refreshSidebar();
                            // Also update the note in the list if it was renamed
                            const note = self.notes.find(function(n) { return n.uuid === e.detail.uuid; });
                            if (note) note.name = e.detail.name;
                            if (self.selectedNote && self.selectedNote.uuid === e.detail.uuid) {
                                self.selectedNote.name = e.detail.name;
                            }
                        })
                        .catch(function() {});
                });
                window.addEventListener('create-group-folder', function(e) {
                    window.fileActions.createGroupFolder(e.detail.groupId, e.detail.groupName)
                        .then(function() { self.refreshSidebar(); })
                        .catch(function() {});
                });
            }

            // Sync reactive prefs and re-sort when sort preference changes
            window.addEventListener('notes:preferences-changed', function(e) {
                const oldSort = this.notePrefs.sortBy;
                this.notePrefs = { ...e.detail };
                // Only reload notes when sort order actually changed
                if (oldSort !== this.notePrefs.sortBy && this.activeView && this.activeView !== 'journal') {
                    this.setView(this.activeView, this.activeId, this.viewTitle, true);
                }
            }.bind(this));

            // Load tags for the editor dropdown
            await this.loadTags();

            // Refresh icons after Alpine renders x-for folders
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });

            // Restore expanded folders from URL
            await this._restoreExpandedFolders();

            // Load initial notes based on SSR state
            if (initialView === 'journal') {
                await this.openJournal();
            } else {
                await this.setView(initialView, config.id, null, true);
            }

            // Auto-open note if specified
            if (config.file) {
                const note = this.notes.find(function(n) { return n.uuid === config.file; });
                if (note) {
                    await this.selectNote(note);
                } else {
                    await this.selectNoteById(config.file);
                }
            }

            // Handle browser back/forward on mobile
            window.addEventListener('popstate', function() {
                const p = new URLSearchParams(window.location.search);
                const fileId = p.get('file');
                if (fileId) {
                    this.selectNoteById(fileId);
                } else {
                    this.selectedNote = null;
                }
            }.bind(this));

            // Keyboard shortcuts
            window.addEventListener('keydown', this._handleShortcut.bind(this));
        },

        _handleShortcut(e) {
            if (e.ctrlKey || e.metaKey || e.altKey) return;
            // Ignore when typing in inputs, textareas, contenteditables
            const ae = document.activeElement;
            const tag = ae ? ae.tagName : '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
                (ae && ae.isContentEditable)) {
                // Allow Escape to blur the active editable element
                if (e.key === 'Escape' && ae && typeof ae.blur === 'function') {
                    ae.blur();
                }
                return;
            }
            // Skip if any modal dialog is open (other than letting Esc close it natively)
            const openDialog = document.querySelector('dialog[open]');
            if (openDialog && e.key !== '?') return;

            // Handle "go to" two-key combos first (g then x)
            if (this._gPending) {
                const key = e.key.toLowerCase();
                this._gPending = false;
                if (key === 'a') { e.preventDefault(); this.setView('all', null, 'All notes'); return; }
                if (key === 'f') { e.preventDefault(); this.setView('favorites', null, 'Favorites'); return; }
                if (key === 'r') { e.preventDefault(); this.setView('recent', null, 'Recent'); return; }
                if (key === 'j') { e.preventDefault(); this.openJournal(); return; }
                // unknown follow-up key falls through to single-key handling
            }

            switch (e.key) {
                case '?':
                    e.preventDefault();
                    const dlg = document.getElementById('notes-help-dialog');
                    if (dlg) dlg.showModal();
                    break;
                case 'n':
                case 'N':
                    e.preventDefault();
                    this.createNote();
                    break;
                case 'j':
                case 'J':
                    e.preventDefault();
                    this._selectAdjacentNote(1);
                    break;
                case 'k':
                case 'K':
                    e.preventDefault();
                    this._selectAdjacentNote(-1);
                    break;
                case 's':
                case 'S':
                    if (this.selectedNote) {
                        e.preventDefault();
                        this.toggleFavorite(this.selectedNote);
                    }
                    break;
                case '#':
                    if (this.selectedNote) {
                        e.preventDefault();
                        this.deleteNote();
                    }
                    break;
                case 'r':
                case 'R':
                    if (this.selectedNote) {
                        e.preventDefault();
                        const input = document.querySelector('input.input-ghost.input-sm.font-semibold');
                        if (input) { input.focus(); input.select(); }
                    }
                    break;
                case 'Escape':
                    if (this.selectedNote) {
                        e.preventDefault();
                        this.selectedNote = null;
                        this.updateUrl();
                    }
                    break;
                case 'g':
                case 'G':
                    e.preventDefault();
                    this._gPending = true;
                    setTimeout(function() { this._gPending = false; }.bind(this), 1000);
                    break;
            }
        },

        _selectAdjacentNote(delta) {
            if (!this.notes || this.notes.length === 0) return;
            let idx = -1;
            if (this.selectedNote) {
                for (let i = 0; i < this.notes.length; i++) {
                    if (this.notes[i].uuid === this.selectedNote.uuid) { idx = i; break; }
                }
            }
            let next = idx + delta;
            if (idx === -1) next = delta > 0 ? 0 : this.notes.length - 1;
            if (next < 0 || next >= this.notes.length) return;
            this.selectNote(this.notes[next]);
        },

        // ── Folder data management (nested tree) ─────────────

        _loadFolderData() {
            const el = document.getElementById('notes-folders-data');
            if (el) {
                try { this.sidebarFolders = JSON.parse(el.textContent); }
                catch (e) { this.sidebarFolders = []; }
            }
            const gel = document.getElementById('notes-group-folders-data');
            if (gel) {
                try { this.sidebarGroupFolders = JSON.parse(gel.textContent); }
                catch (e) { this.sidebarGroupFolders = []; }
            }
            // Root folders are at depth 0
            this.sidebarFolders.forEach(function(f) { f.depth = 0; });
            // Filter out the journal folder from sidebar (it has its own Quick Access entry)
            const journalUuid = this.notePrefs.journalFolderUuid;
            if (journalUuid) {
                this.sidebarFolders = this.sidebarFolders.filter(function(f) { return f.uuid !== journalUuid; });
            }
            this.sidebarGroupFolders.forEach(function(f) { f.depth = 0; });
        },

        _findFolder(uuid, list) {
            if (!list) return null;
            for (let i = 0; i < list.length; i++) {
                if (list[i].uuid === uuid) return list[i];
                const found = this._findFolder(uuid, list[i].children);
                if (found) return found;
            }
            return null;
        },

        async _loadChildren(folder) {
            if (folder.children) return; // already loaded
            this.loadingChildren = this.loadingChildren.concat([folder.uuid]);

            const resp = await fetch('/api/v1/files?parent=' + folder.uuid + '&node_type=folder&ordering=name');
            this.loadingChildren = this.loadingChildren.filter(function(id) { return id !== folder.uuid; });
            if (!resp.ok) { folder.children = []; return; }

            const children = await resp.json();
            const childDepth = (folder.depth || 0) + 1;
            children.forEach(function(c) { c.depth = childDepth; });
            folder.children = children;

            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        // ── Sidebar navigation ──────────────────────────────

        _sortParam() {
            const sortMap = { name: 'name', modified: '-updated_at', created: '-created_at' };
            return sortMap[window._notesPrefsCache.sortBy] || '-updated_at';
        },

        async setView(view, id, name, skipUrl, descendants) {
            id = id || null;
            const viewChanged = (view !== this.activeView || id !== this.activeId);
            this.activeView = view;
            this.activeId = id;
            this._descendants = !!descendants;
            this._closeDrawerOnMobile();

            if (view === 'all') {
                this.viewTitle = 'All notes';
            } else if (view === 'favorites') {
                this.viewTitle = 'Favorites';
            } else if (view === 'recent') {
                this.viewTitle = 'Recent';
            } else if (view === 'tag') {
                if (!name && id) {
                    const tagEl = document.querySelector('[data-tag-uuid="' + id + '"]');
                    if (tagEl) name = tagEl.dataset.tagName;
                }
                this.viewTitle = name || 'Tag';
            } else if (view === 'folder' || view === 'group_folder') {
                this.viewTitle = name || 'Folder';
            } else {
                this.viewTitle = 'All notes';
                this.activeView = 'all';
            }

            if (viewChanged) {
                this._resetFilters();
            }

            await this.loadNotes(this._buildNotesUrl());

            if (!skipUrl) {
                this.selectedNote = null;
                this.updateUrl();
            }
        },

        async loadNotes(url) {
            this.notes = [];
            this.loadingNotes = true;
            const resp = await fetch(url);
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
            this._closeDrawerOnMobile();

            const journalUuid = this.notePrefs.journalFolderUuid;
            if (!journalUuid) return;

            this.activeId = journalUuid;
            await this.loadNotes('/api/v1/files?mime_type=text/markdown&parent=' + journalUuid + '&ordering=-name');

            // Create today's note if needed
            const today = new Date().toISOString().split('T')[0];
            const todayName = today + '.md';
            let todayNote = this.notes.find(function(n) { return n.name === todayName; });

            if (!todayNote) {
                todayNote = await this._createMdFile(todayName, journalUuid);
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

        canDoOnSelected(actionId) {
            return this.selectedNoteActionIds.indexOf(actionId) !== -1;
        },

        async _fetchActionsForSelected(uuid) {
            const gen = ++this._actionsFetchGen;
            try {
                const resp = await fetch('/api/v1/files/actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ uuids: [uuid] }),
                });
                if (gen !== this._actionsFetchGen) return;
                if (!resp.ok) return;
                const data = await resp.json();
                if (gen !== this._actionsFetchGen) return;
                const list = data[uuid] || [];
                this.selectedNoteActionIds = list.map(function(a) { return a.id; });
            } catch (e) {
                if (gen === this._actionsFetchGen) {
                    this.selectedNoteActionIds = [];
                }
            }
        },

        async selectNote(note) {
            this.selectedNote = note;
            this.selectedNoteActionIds = [];
            this.updateUrl({push: this.isMobile()});
            await this.$nextTick();
            // Fetch available actions in parallel with the viewer — both are
            // independent HTTP calls, no sequential dependency.
            this._fetchActionsForSelected(note.uuid);
            await this.loadViewer(note);
        },

        async selectNoteById(uuid) {
            if (!isValidUuid(uuid)) return;
            const resp = await fetch('/api/v1/files/' + uuid);
            if (resp.ok) {
                const note = await resp.json();
                await this.selectNote(note);
            }
        },

        async loadViewer(note) {
            const container = this.$refs.editorContainer;
            if (!container) return;

            // Cleanup previous viewer
            window.dispatchEvent(new CustomEvent('viewer-cleanup'));
            container.replaceChildren();
            this._loadedScripts.forEach(function(s) { s.remove(); });
            this._loadedScripts = [];

            const generation = ++this._loadGeneration;
            this.loadingEditor = true;

            try {
                const resp = await fetch('/files/view/' + note.uuid);
                if (generation !== this._loadGeneration) return;
                if (!resp.ok) throw new Error('Failed to load viewer: ' + resp.status);

                const rawHtml = await resp.text();
                if (generation !== this._loadGeneration) return;

                const temp = document.createElement('template');
                temp.innerHTML = rawHtml;

                const scriptEls = temp.content.querySelectorAll('script');
                const scripts = [];
                scriptEls.forEach(function(el) {
                    scripts.push(el.textContent);
                    el.remove();
                });

                scripts.forEach(function(scriptContent) {
                    const newScript = document.createElement('script');
                    newScript.textContent = scriptContent;
                    document.head.appendChild(newScript);
                    this._loadedScripts.push(newScript);
                }.bind(this));

                while (temp.content.firstChild) {
                    container.appendChild(temp.content.firstChild);
                }
            } catch (err) {
                if (generation !== this._loadGeneration) return;
                container.textContent = err.message;
            } finally {
                if (generation === this._loadGeneration) {
                    this.loadingEditor = false;
                }
            }
        },

        // ── Note CRUD ───────────────────────────────────────

        async createNote() {
            let name = await AppDialog.prompt({
                title: 'New note',
                message: 'Enter a name for the note',
                placeholder: 'My note',
                okLabel: 'Create',
                okClass: 'btn-success',
                icon: 'file-plus',
                iconClass: 'bg-success/10 text-success',
            });
            if (!name) return;
            if (!name.endsWith('.md')) name += '.md';

            let parentUuid;
            if (this.activeView === 'folder' || this.activeView === 'group_folder') {
                parentUuid = this.activeId;
            } else if (this.activeView === 'journal') {
                parentUuid = this.notePrefs.journalFolderUuid || this.activeId;
            } else {
                parentUuid = this.notePrefs.defaultFolderUuid || null;
            }
            const note = await this._createMdFile(name, parentUuid);
            if (note) {
                this.notes.unshift(note);
                await this.selectNote(note);
            }
        },

        async renameNote(newName) {
            if (!this.selectedNote || !newName) return;
            if (!this.canDoOnSelected('rename')) return;
            if (!newName.endsWith('.md')) newName += '.md';
            if (newName === this.selectedNote.name) return;

            const resp = await fetch('/api/v1/files/' + this.selectedNote.uuid, {
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
            if (!this.canDoOnSelected('delete')) return;
            if (window._notesPrefsCache.confirmBeforeDelete) {
                const ok = await AppDialog.confirm({
                    title: 'Delete note',
                    message: 'Are you sure you want to delete "' + this.noteName(this.selectedNote) + '"?',
                    okLabel: 'Delete',
                    okClass: 'btn-error',
                    icon: 'trash-2',
                    iconClass: 'bg-error/10 text-error',
                });
                if (!ok) return;
            }

            window.dispatchEvent(new CustomEvent('viewer-cleanup'));

            const resp = await fetch('/api/v1/files/' + this.selectedNote.uuid, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });

            if (resp.ok) {
                const uuid = this.selectedNote.uuid;
                this.notes = this.notes.filter(function(n) { return n.uuid !== uuid; });
                const container = this.$refs.editorContainer;
                if (container) container.replaceChildren();
                this._loadedScripts.forEach(function(s) { s.remove(); });
                this._loadedScripts = [];
                this.selectedNote = null;
                this.updateUrl();
            }
        },

        async toggleFavorite(note) {
            if (!note || this.togglingFavorite) return;
            // Gate only when toggling from the editor toolbar (selected note).
            // List/context-menu calls already filter on their own fetched action list.
            if (
                this.selectedNote
                && note.uuid === this.selectedNote.uuid
                && !this.canDoOnSelected('toggle_favorite')
            ) {
                return;
            }
            this.togglingFavorite = true;
            const isFav = note.is_favorite;
            const resp = await fetch('/api/v1/files/' + note.uuid + '/favorite', {
                method: isFav ? 'DELETE' : 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });
            if (resp.ok) {
                note.is_favorite = !isFav;
                if (this.activeView === 'favorites' && isFav) {
                    const idx = this.notes.findIndex(function(n) { return n.uuid === note.uuid; });
                    this.notes = this.notes.filter(function(n) { return n.uuid !== note.uuid; });
                    if (this.selectedNote && this.selectedNote.uuid === note.uuid) {
                        const next = this.notes[idx] || this.notes[idx - 1] || null;
                        if (next) {
                            this.selectNote(next);
                        } else {
                            this.selectedNote = null;
                            this.updateUrl();
                        }
                    }
                }
            }
            this.togglingFavorite = false;
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
            let x = e.clientX;
            let y = e.clientY;
            // Prevent overflow
            const menuW = 220, menuH = 200;
            if (x + menuW > window.innerWidth) x = window.innerWidth - menuW;
            if (y + menuH > window.innerHeight) y = window.innerHeight - menuH;
            this.ctxMenu = { open: true, x: x, y: y, type: type, data: data, actions: null };

            // Fetch dynamic actions for folder and note types
            if (type === 'folder' || type === 'group_folder') {
                this._fetchFolderActions(data.uuid);
            } else if (type === 'note') {
                this._fetchNoteActions(data.uuid);
            }
        },

        async _fetchFolderActions(uuid) {
            try {
                const resp = await fetch('/api/v1/files/actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ uuids: [uuid] }),
                });
                if (resp.ok) {
                    const data = await resp.json();
                    const allActions = data[uuid] || [];
                    // Filter to relevant folder actions for the notes sidebar
                    const relevant = ['rename', 'delete'];
                    this.ctxMenu.actions = allActions.filter(function(a) {
                        return relevant.indexOf(a.id) !== -1;
                    });
                } else {
                    this.ctxMenu.actions = [];
                }
            } catch (e) {
                this.ctxMenu.actions = [];
            }
            // Refresh icons in the context menu
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        async _fetchNoteActions(uuid) {
            try {
                const resp = await fetch('/api/v1/files/actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ uuids: [uuid] }),
                });
                if (resp.ok) {
                    const data = await resp.json();
                    const allActions = data[uuid] || [];
                    // Show relevant note actions (favorite, rename, delete)
                    const relevant = ['toggle_favorite', 'rename', 'delete'];
                    this.ctxMenu.actions = allActions.filter(function(a) {
                        return relevant.indexOf(a.id) !== -1;
                    });
                } else {
                    this.ctxMenu.actions = [];
                }
            } catch (e) {
                this.ctxMenu.actions = [];
            }
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        ctxNoteAction(action) {
            const m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            const self = this;
            const uuid = m.data.uuid;
            const name = m.data.name;

            if (action.id === 'toggle_favorite') {
                const note = this.notes.find(function(n) { return n.uuid === uuid; });
                if (note) this.toggleFavorite(note);
            } else if (action.id === 'rename') {
                // Select the note first, then trigger rename via the editor header
                const note = this.notes.find(function(n) { return n.uuid === uuid; });
                if (note) {
                    this.selectNote(note).then(function() {
                        self.showRenameDialog(uuid, name);
                    });
                }
            } else if (action.id === 'delete') {
                AppDialog.confirm({
                    title: 'Delete note',
                    message: 'Are you sure you want to delete "' + (name || '').replace(/\.md$/i, '') + '"?',
                    okLabel: 'Delete',
                    okClass: 'btn-error',
                    icon: 'trash-2',
                    iconClass: 'bg-error/10 text-error',
                }).then(function(ok) {
                    if (!ok) return;
                    fetch('/api/v1/files/' + uuid, {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCSRFToken() },
                    }).then(function(resp) {
                        if (!resp.ok) return;
                        self.notes = self.notes.filter(function(n) { return n.uuid !== uuid; });
                        if (self.selectedNote && self.selectedNote.uuid === uuid) {
                            const container = self.$refs.editorContainer;
                            if (container) container.replaceChildren();
                            self._loadedScripts.forEach(function(s) { s.remove(); });
                            self._loadedScripts = [];
                            self.selectedNote = null;
                            self.updateUrl();
                        }
                    });
                });
            } else if (action.id === 'move') {
                this.moveNote(uuid, name);
            }
        },

        async moveNote(uuid, name) {
            const displayName = (name || '').replace(/\.md$/i, '');
            const folder = await AppDialog.folderPicker({
                title: 'Move note',
                message: 'Choose a destination for "' + displayName + '"',
                okLabel: 'Move here',
                okClass: 'btn-primary',
                icon: 'folder-input',
                iconClass: 'bg-primary/10 text-primary',
            });
            if (!folder) return;

            const resp = await fetch('/api/v1/files/' + uuid, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ parent: folder.uuid }),
            });
            if (resp.ok) {
                // Remove from current list if we're in a folder view and the note moved out
                const self = this;
                if (this.activeView === 'folder' || this.activeView === 'group_folder') {
                    this.notes = this.notes.filter(function(n) { return n.uuid !== uuid; });
                    if (self.selectedNote && self.selectedNote.uuid === uuid) {
                        self.selectedNote = null;
                        self.updateUrl();
                    }
                }
                this.refreshSidebar();
            }
        },

        closeCtxMenu() {
            this.ctxMenu.open = false;
        },

        ctxFolderAction(action) {
            const m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            if (action.id === 'rename') {
                this.showRenameDialog(m.data.uuid, m.data.name);
            } else if (action.id === 'delete') {
                const self = this;
                const uuid = m.data.uuid;
                const name = m.data.name;
                AppDialog.confirm({
                    title: 'Delete folder',
                    message: 'Move "' + name + '" to trash? Notes inside will also be moved.',
                    okLabel: 'Move to trash',
                    okClass: 'btn-error',
                    icon: 'trash-2',
                    iconClass: 'bg-error/10 text-error',
                }).then(function(ok) {
                    if (!ok) return;
                    fetch('/api/v1/files/' + uuid, {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCSRFToken() },
                    }).then(function(resp) {
                        if (resp.ok) self.refreshSidebar();
                    });
                });
            } else if (action.id === 'create_subfolder') {
                this._createSubfolder(m.data);
            }
        },

        async _createSubfolder(parentFolder) {
            const name = await AppDialog.prompt({
                title: 'New subfolder',
                message: 'Create a subfolder in "' + parentFolder.name + '"',
                placeholder: 'Subfolder name',
                okLabel: 'Create',
                okClass: 'btn-success',
                icon: 'folder-plus',
                iconClass: 'bg-success/10 text-success',
            });
            if (!name) return;

            const body = { name: name, node_type: 'folder', parent: parentFolder.uuid };
            const resp = await fetch('/api/v1/files', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify(body),
            });
            if (!resp.ok) return;

            const created = await resp.json();
            created.depth = (parentFolder.depth || 0) + 1;
            created.has_children = false;

            // Insert into parent's children list
            if (!parentFolder.children) parentFolder.children = [];
            parentFolder.children.push(created);
            parentFolder.has_children = true;

            // Auto-expand parent if not already
            if (this.expandedFolders.indexOf(parentFolder.uuid) === -1) {
                this.expandedFolders = this.expandedFolders.concat([parentFolder.uuid]);
                this._writeExpandedToUrl();
            }

            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        ctxAction(action) {
            const m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            if (m.type === 'tag') {
                if (action === 'edit') {
                    this.showTagModal(m.data);
                } else if (action === 'delete') {
                    if (!confirm('Delete tag "' + m.data.name + '"?')) return;
                    const self = this;
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


        // ── Expanded folders (URL-based) ────────────────────

        expandedFolders: [],

        _readExpandedFromUrl() {
            const p = new URLSearchParams(window.location.search);
            const raw = p.get('expanded');
            return raw ? raw.split(',').filter(Boolean) : [];
        },

        _writeExpandedToUrl() {
            const url = new URL(window.location);
            if (this.expandedFolders.length > 0) {
                url.searchParams.set('expanded', this.expandedFolders.join(','));
            } else {
                url.searchParams.delete('expanded');
            }
            window.history.replaceState({}, '', url);
        },

        async _restoreExpandedFolders() {
            const uuids = this._readExpandedFromUrl();
            if (uuids.length === 0) return;
            this.expandedFolders = uuids;
            // Lazy-load children for each expanded folder in order
            for (let i = 0; i < uuids.length; i++) {
                const folder = this._findFolder(uuids[i], this.sidebarFolders)
                          || this._findFolder(uuids[i], this.sidebarGroupFolders);
                if (folder) {
                    await this._loadChildren(folder);
                }
            }
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        async toggleFolderExpand(uuid) {
            const idx = this.expandedFolders.indexOf(uuid);
            if (idx === -1) {
                const folder = this._findFolder(uuid, this.sidebarFolders)
                          || this._findFolder(uuid, this.sidebarGroupFolders);
                if (folder) {
                    await this._loadChildren(folder);
                }
                this.expandedFolders = this.expandedFolders.concat([uuid]);
            } else {
                // Collapsing: also remove expanded descendants
                const toRemove = this._getDescendantUuids(uuid);
                toRemove.push(uuid);
                this.expandedFolders = this.expandedFolders.filter(function(id) {
                    return toRemove.indexOf(id) === -1;
                });
            }
            this._writeExpandedToUrl();
        },

        _getDescendantUuids(uuid) {
            const folder = this._findFolder(uuid, this.sidebarFolders)
                      || this._findFolder(uuid, this.sidebarGroupFolders);
            if (!folder || !folder.children) return [];
            const result = [];
            function walk(children) {
                for (let i = 0; i < children.length; i++) {
                    result.push(children[i].uuid);
                    if (children[i].children) walk(children[i].children);
                }
            }
            walk(folder.children);
            return result;
        },

        toggleHidden(uuid) {
            const list = (this.notePrefs.hiddenItems || []).slice();
            const idx = list.indexOf(uuid);
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
            const prefs = { ...window._notesPrefsCache };
            fetch('/api/v1/settings/notes/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ value: prefs }),
            }).catch(function() {});
        },

        // ── URL state ────────────────────────────────────────

        updateUrl(options) {
            options = options || {};
            const push = options.push || false;
            const url = new URL(window.location);
            url.search = '';

            if (this.activeView !== 'all') {
                url.searchParams.set('view', this.activeView);
            }
            if (this.activeView === 'tag' && this.activeId) {
                url.searchParams.set('tag', this.activeId);
            }
            if ((this.activeView === 'folder' || this.activeView === 'group_folder') && this.activeId) {
                url.searchParams.set('folder', this.activeId);
            }
            if (this.selectedNote) {
                url.searchParams.set('file', this.selectedNote.uuid);
            }
            if (this.expandedFolders.length > 0) {
                url.searchParams.set('expanded', this.expandedFolders.join(','));
            }

            if (push) {
                window.history.pushState({}, '', url);
            } else {
                window.history.replaceState({}, '', url);
            }
        },

        // ── Sidebar refresh ──────────────────────────────────

        async refreshSidebar() {
            const resp = await fetch('/notes', {
                headers: { 'X-Alpine-Request': 'true' },
            });
            if (resp.ok) {
                const html = await resp.text();
                const container = document.getElementById('notes-sidebar');
                if (container) {
                    container.textContent = '';
                    // Parse and insert safely
                    const temp = document.createElement('template');
                    temp.innerHTML = html;
                    container.appendChild(temp.content);
                    // Reload folder data from new embedded JSON
                    this._loadedChildren = {};
                    this._loadFolderData();
                    // Re-init Lucide icons in the new HTML
                    if (window.lucide) window.lucide.createIcons();
                }
            }
        },

        // ── Helpers ─────────────────────────────────────────

        _buildNotesUrl() {
            const sort = '&ordering=' + this._sortParam();
            let base = '/api/v1/files?mime_type=text/markdown';
            const hasSearch = this.filters.search.trim();

            if (this.activeView === 'all') {
                if (!hasSearch) base += '&recent=1&recent_limit=200';
                base += sort;
            } else if (this.activeView === 'favorites') {
                base += '&favorites=1' + sort;
            } else if (this.activeView === 'recent') {
                if (!hasSearch) base += '&recent=1&recent_limit=50';
                base += sort;
            } else if (this.activeView === 'tag') {
                if (!hasSearch) base += '&recent=1&recent_limit=200';
                base += '&tags=' + this.activeId + sort;
            } else if (this.activeView === 'folder' || this.activeView === 'group_folder') {
                base += '&parent=' + this.activeId + sort;
                if (this._descendants) base += '&descendants=1';
            } else if (this.activeView === 'journal') {
                base += '&parent=' + this.activeId + '&ordering=-name';
            } else {
                if (!hasSearch) base += '&recent=1&recent_limit=200';
                base += sort;
            }

            // Append filter params
            if (hasSearch) {
                base += '&search=' + encodeURIComponent(this.filters.search.trim());
            }
            if (this.filters.favorites && this.activeView !== 'favorites') {
                base += '&favorites=1';
            }
            if (this.filters.tags.length > 0) {
                let filterTags = this.filters.tags;
                if (this.activeView === 'tag' && this.activeId) {
                    filterTags = filterTags.filter(function(t) { return t !== this.activeId; }.bind(this));
                }
                if (filterTags.length > 0) {
                    base += (base.indexOf('&tags=') === -1 ? '&tags=' : ',') + filterTags.join(',');
                }
            }

            return base;
        },

        _hasActiveFilters() {
            return !!(this.filters.search || this.filters.favorites || this.filters.tags.length);
        },

        _resetFilters() {
            this.filters = { search: '', favorites: false, tags: [] };
            if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
            this.showTagDropdown = false;
        },

        applyFilters() {
            this.loadNotes(this._buildNotesUrl());
        },

        toggleFilter(name) {
            this.filters[name] = !this.filters[name];
            this.applyFilters();
        },

        toggleTagFilter(tagUuid) {
            const idx = this.filters.tags.indexOf(tagUuid);
            if (idx === -1) {
                this.filters.tags.push(tagUuid);
            } else {
                this.filters.tags.splice(idx, 1);
            }
            this.applyFilters();
        },

        onSearchInput() {
            if (this._searchTimer) clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(function() { this.applyFilters(); }.bind(this), 400);
        },

        clearFilters() {
            this._resetFilters();
            this.applyFilters();
        },

        highlightSearch(text) {
            if (!text) return '';
            const escaped = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            const q = this.filters.search.trim();
            if (!q) return escaped;
            const re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
            return escaped.replace(re, '<mark class="bg-warning/40 text-inherit rounded-sm px-0.5">$1</mark>');
        },

        selectedTagNames() {
            const selected = this.filters.tags;
            if (!selected.length) return '';
            const names = this.allTags
                .filter(function(t) { return selected.indexOf(t.uuid) !== -1; })
                .map(function(t) { return t.name; });
            if (names.length <= 2) return names.join(', ');
            return names.slice(0, 2).join(', ') + ' +' + (names.length - 2);
        },

        async _createMdFile(name, parentUuid) {
            const formData = new FormData();
            formData.append('name', name);
            formData.append('node_type', 'file');
            formData.append('mime_type', 'text/markdown');
            formData.append('content', new Blob([''], { type: 'text/markdown' }), name);
            if (parentUuid) {
                formData.append('parent', parentUuid);
            }

            const resp = await fetch('/api/v1/files', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
                body: formData,
            });

            if (resp.ok) {
                const note = await resp.json();
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
            const d = new Date(dateStr);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },

        toggleCollapse() {
            this.collapsed = !this.collapsed;
            localStorage.setItem('notes-sidebar-collapsed', this.collapsed);
        },

        isMobile() {
            return window.innerWidth < 1024;
        },

        _closeDrawerOnMobile() {
            if (this.isMobile()) {
                const toggle = document.getElementById('notes-drawer');
                if (toggle) toggle.checked = false;
            }
        },

        destroy() {
            window.dispatchEvent(new CustomEvent('viewer-cleanup'));
            this._loadedScripts.forEach(function(s) { s.remove(); });
            this._loadedScripts = [];
        },
    };
};
