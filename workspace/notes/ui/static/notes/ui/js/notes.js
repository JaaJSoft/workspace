// ── Notes Preferences ────────────────────────────────────
window._notesPrefsDefaults = {
    showTags: true,
    compactList: false,
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
};
// Initial prefs are embedded server-side via |json_script (see notes.html).
// Reading them synchronously means the first Alpine paint already has the
// right sections and view - no reshuffle once a fetch lands.
(function bootNotesPrefs() {
    let initial = {};
    const el = document.getElementById('notes-prefs-data');
    if (el) {
        try { initial = JSON.parse(el.textContent) || {}; } catch (e) { initial = {}; }
    }
    window._notesPrefsCache = { ...window._notesPrefsDefaults, ...initial };
})();

window.notesPreferences = function notesPreferences() {
    const API_URL = '/api/v1/settings/notes/preferences';
    let _saveTimer = null;

    return {
        prefs: { ...window._notesPrefsCache },
        defaultFolderName: '',
        journalFolderName: '',

        async init() {
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
// Rows per request. The list is not virtualized, so this also caps how
// much DOM one scroll step adds.
const NOTES_PAGE_SIZE = 100;

window.notesApp = function notesApp(config) {
    config = config || {};
    const prefs = window._notesPrefsCache;
    const initialView = config.view || prefs.defaultView || 'all';
    const titleMap = { all: 'My Notes', favorites: 'Favorites', recent: 'Recent', journal: 'Journal', graph: 'Graph' };

    return {
        // Sidebar
        collapsed: window.sidebarPreference.initial(),
        activeView: initialView,
        activeId: config.id || null,
        viewTitle: titleMap[initialView] || 'My Notes',
        graphScope: 'mine',
        graphKind: 'all',
        graphSearch: '',
        graphTags: [],
        showGraphTagDropdown: false,
        graphTagSearch: '',
        graphLoading: false,

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
        loadingMoreNotes: false,
        hasMoreNotes: false,
        // Bumped by every fresh load; an in-flight page whose generation no
        // longer matches belongs to a view the user has already left.
        _notesGeneration: 0,
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

        // Editor (viewerLoading / viewerError / loadViewerPanel come from
        // the shared panel mixin; the viewer HTML merges into #viewer-panel)
        ...window.viewerPanelMixin(),
        selectedNote: null,

        // Available actions on the currently selected note — driven by
        // POST /api/v1/files/actions. Empty list = fail-safe (buttons disabled).
        selectedNoteActionIds: [],
        _actionsFetchGen: 0,

        async init() {
            // Load folder data from embedded JSON
            this._loadFolderData();

            // Listen for sidebar refresh events
            window.addEventListener('tags-changed', this.refreshSidebar.bind(this));

            // Catch up on notes changed elsewhere while the stream was down
            // (resumed tab, or a bfcache restore after a mobile back).
            window.addEventListener('sse:reconnect', () => this.resync());

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
                // Unhiding folders re-creates their sidebar rows (flat x-for),
                // so the icons must be re-rendered.
                this.$nextTick(function() {
                    if (window.lucide) window.lucide.createIcons();
                });
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

            this.$nextTick(function() { this._setupNotesObserver(); }.bind(this));

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
                if (key === 'a') { e.preventDefault(); this.setView('all', null, 'My Notes'); return; }
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

        // Preorder walk of the loaded tree, pruned at collapsed folders and
        // (unless showHidden) at hidden subtrees. The sidebar renders these
        // rows as one flat list indented by `depth`, so nesting is unbounded.
        visibleFolderRows(list) {
            const rows = [];
            const showHidden = this.notePrefs.showHidden;
            const walk = (nodes) => {
                for (const node of nodes || []) {
                    if (!showHidden && this.isHidden(node.uuid)) continue;
                    rows.push(node);
                    if (this.expandedFolders.indexOf(node.uuid) !== -1 && node.children) {
                        walk(node.children);
                    }
                }
            };
            walk(list);
            return rows;
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

            if (view === 'graph') {
                this.viewTitle = 'Graph';
            } else if (view === 'all') {
                this.viewTitle = 'My Notes';
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
                this.viewTitle = 'My Notes';
                this.activeView = 'all';
            }

            if (viewChanged) {
                this._resetFilters();
            }

            if (view === 'graph') {
                this.$nextTick(() => this._openGraph());
            } else {
                this._disposeGraph();
                await this.loadNotes(this._buildNotesUrl());
            }

            if (!skipUrl) {
                this.selectedNote = null;
                this.updateUrl();
            }
        },

        async loadNotes(url, options) {
            const append = !!(options && options.append);
            const generation = append ? this._notesGeneration : ++this._notesGeneration;
            if (append) {
                this.loadingMoreNotes = true;
            } else {
                this.notes = [];
                this.hasMoreNotes = false;
                this.loadingNotes = true;
            }
            try {
                const resp = await fetch(url);
                if (generation !== this._notesGeneration) return;
                if (resp.ok) {
                    const page = await resp.json();
                    if (generation !== this._notesGeneration) return;
                    // The endpoint answers a bare array, so the header is the
                    // only thing that knows whether a further page exists.
                    this.hasMoreNotes = resp.headers.get('X-Has-More') === 'true';
                    this.notes = append ? this._mergeNotes(this.notes, page) : page;
                }
            } finally {
                if (generation === this._notesGeneration) {
                    this.loadingNotes = false;
                    this.loadingMoreNotes = false;
                }
            }
        },

        // A note created between two pages shifts the window, so the next
        // page can legitimately repeat a row the list already holds.
        _mergeNotes(current, page) {
            const seen = new Set(current.map(function(n) { return n.uuid; }));
            return current.concat(page.filter(function(n) { return !seen.has(n.uuid); }));
        },

        async loadMoreNotes() {
            if (!this.hasMoreNotes || this.loadingNotes || this.loadingMoreNotes) return;
            // Derived rather than tracked: deleting or inserting a note locally
            // shortens the server's list by the same row, and a stale counter
            // would step over the note that moved into the gap. Erring small
            // only costs an overlap, which _mergeNotes drops.
            await this.loadNotes(this._buildNotesUrl(this.notes.length), { append: true });
        },

        _setupNotesObserver() {
            if (this._notesObserver) return;
            // No IntersectionObserver: the fallback button in the template is
            // the only way to page, and it stays visible for that reason.
            if (typeof IntersectionObserver === 'undefined') return;
            const sentinel = this.$refs.notesSentinel;
            if (!sentinel) return;

            this._notesObserver = new IntersectionObserver(function(entries) {
                for (const entry of entries) {
                    // loadMoreNotes() guards on its own state, so the observer
                    // is free to fire on every scroll tick.
                    if (entry.isIntersecting) this.loadMoreNotes();
                }
            }.bind(this), { root: sentinel.parentElement, rootMargin: '300px 0px', threshold: 0 });

            this._notesObserver.observe(sentinel);
        },

        _openGraph() {
            if (!window.notesGraph || !this.$refs.graphCanvas) return;
            window.notesGraph.open(this.$refs.graphCanvas, {
                journalUuid: this.notePrefs.journalFolderUuid || null,
                notesRoot: this.notePrefs.defaultFolderUuid || null,
                scope: this.graphScope,
                onNodeClick: (uuid) => this.openNoteFromGraph(uuid),
                onLoading: (loading) => { this.graphLoading = loading; },
            });
        },

        _disposeGraph() {
            if (window.notesGraph && window.notesGraph.destroy) window.notesGraph.destroy();
            this.graphKind = 'all';
            this.graphSearch = '';
            this.graphTags = [];
            this.showGraphTagDropdown = false;
            this.graphTagSearch = '';
            this.graphLoading = false;
        },

        setGraphScope(scope) {
            this.graphScope = scope;
            if (window.notesGraph) window.notesGraph.setScope(scope);
        },

        setGraphKind(kind) {
            this.graphKind = kind;
            if (window.notesGraph) window.notesGraph.setKind(kind);
        },

        resetGraphView() {
            if (window.notesGraph) window.notesGraph.fitView();
        },

        onGraphSearch() {
            if (window.notesGraph) window.notesGraph.setSearch(this.graphSearch);
        },

        toggleGraphTag(tagUuid) {
            const idx = this.graphTags.indexOf(tagUuid);
            if (idx === -1) {
                this.graphTags.push(tagUuid);
            } else {
                this.graphTags.splice(idx, 1);
            }
            if (window.notesGraph) window.notesGraph.setTags(this.graphTags);
        },

        clearGraphTags() {
            this.graphTags = [];
            if (window.notesGraph) window.notesGraph.setTags(this.graphTags);
        },

        // Tags matching the in-dropdown search box (case-insensitive substring).
        // An empty query returns the full list, so the box only narrows.
        filteredGraphTags() {
            const q = this.graphTagSearch.trim().toLowerCase();
            if (!q) return this.allTags;
            return this.allTags.filter(function(t) {
                return t.name.toLowerCase().indexOf(q) !== -1;
            });
        },

        // Comma-joined names of the tags currently filtering the graph, for the
        // dropdown button label (mirrors selectedTagNames() in the note list).
        selectedGraphTagNames() {
            const selected = this.graphTags;
            return this.allTags
                .filter(function(t) { return selected.indexOf(t.uuid) !== -1; })
                .map(function(t) { return t.name; })
                .join(', ');
        },

        openNoteFromGraph(uuid) {
            // Leave the graph and open the note. A navigation keeps this robust
            // (the index view opens ?file= on load); the graph disposes first.
            this._disposeGraph();
            window.location.href = '/notes?file=' + encodeURIComponent(uuid);
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
            await this.loadNotes('/api/v1/files?type=markdown&parent=' + journalUuid + '&ordering=-name');

            // Create today's note if needed ("today" in the user's timezone)
            const today = window.userTzDayKey
                ? window.userTzDayKey(new Date())
                : new Date().toISOString().split('T')[0];
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

        // Build the "Open in Files" URL: land in the note's parent folder
        // (path segment) with the file viewer opened (?open=). Falls back to
        // the files root when the note lives at the top level.
        openInFilesHref(note) {
            if (!note || !note.uuid) return '/files';
            const folder = note.parent ? '/' + note.parent : '';
            return '/files' + folder + '?open=' + note.uuid;
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
            await this.loadViewerPanel('/files/view/' + note.uuid);
        },

        async selectNoteById(uuid) {
            if (!isValidUuid(uuid)) return;
            const resp = await fetch('/api/v1/files/' + uuid);
            if (resp.ok) {
                const note = await resp.json();
                await this.selectNote(note);
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

            // Dispose the mounted editor (and release its file lock) before
            // the note it edits goes away; a still-loading viewer must not
            // mount once the note is deleted.
            this.teardownViewerPanel();

            const resp = await fetch('/api/v1/files/' + this.selectedNote.uuid, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });

            if (resp.ok) {
                const uuid = this.selectedNote.uuid;
                this.notes = this.notes.filter(function(n) { return n.uuid !== uuid; });
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
                            self.teardownViewerPanel();
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
            // Collapsed rows leave the DOM (flat x-for), so re-expanding
            // re-creates their elements and the icons must be re-rendered.
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
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
            // $ajax rejects when the swap can't happen (network error, or an
            // error response missing the target id); a failed refresh keeps
            // the current sidebar, matching the old silent-failure behavior.
            // Lucide icons in the merged subtree are re-rendered by the
            // global observeLucideIcons() observer from base.html.
            try {
                await this.$ajax('/notes', { target: 'notes-sidebar', focus: false });
            } catch (e) {
                return;
            }
            // The swap replaced the embedded folder JSON: re-read it and drop
            // lazily-fetched children so they reload against the new tree.
            this._loadedChildren = {};
            this._loadFolderData();
            // The fresh tree only carries root folders - re-fetch children of
            // the folders the user had expanded (tracked in the URL), or their
            // rows vanish until collapsed and expanded again.
            await this._restoreExpandedFolders();
        },

        // ── Helpers ─────────────────────────────────────────

        _buildNotesUrl(offset) {
            const sort = '&ordering=' + this._sortParam();
            let base = '/api/v1/files?type=markdown';
            const hasSearch = this.filters.search.trim();

            if (this.activeView === 'all') {
                const notesRoot = this.notePrefs.defaultFolderUuid;
                if (notesRoot) {
                    // "My Notes": everything under the Notes folder, recursively,
                    // minus the Journal subtree. Search (added later) stays scoped.
                    base += '&parent=' + notesRoot + '&descendants=1';
                    if (this.notePrefs.journalFolderUuid) {
                        base += '&exclude_descendants_of=' + this.notePrefs.journalFolderUuid;
                    }
                } else if (!hasSearch) {
                    // Prefs not ready: fall back to the legacy "all markdown" list.
                    base += '&recent=1';
                }
                base += sort;
            } else if (this.activeView === 'favorites') {
                base += '&favorites=1' + sort;
            } else if (this.activeView === 'recent') {
                if (!hasSearch) base += '&recent=1';
                base += sort;
            } else if (this.activeView === 'tag') {
                if (!hasSearch) base += '&recent=1';
                base += '&tags=' + this.activeId + sort;
            } else if (this.activeView === 'folder' || this.activeView === 'group_folder') {
                base += '&parent=' + this.activeId + sort;
                if (this._descendants) base += '&descendants=1';
            } else if (this.activeView === 'journal') {
                base += '&parent=' + this.activeId + '&ordering=-name';
            } else {
                if (!hasSearch) base += '&recent=1';
                base += sort;
            }

            base += '&limit=' + NOTES_PAGE_SIZE + '&offset=' + (offset || 0);

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

        async resync() {
            this.refreshSidebar();
            // The graph owns its own data and is not driven by loadNotes().
            if (this.activeView === 'graph') return;
            // Reload every page the user had scrolled through, not just the
            // first: refetching one page would shrink the list under them.
            const restore = Math.max(this.notes.length, NOTES_PAGE_SIZE);
            await this.loadNotes(this._buildNotesUrl());
            while (this.hasMoreNotes && this.notes.length < restore) {
                const before = this.notes.length;
                await this.loadMoreNotes();
                // A failed page leaves hasMoreNotes true and the list the same
                // length, which would spin here forever.
                if (this.notes.length === before) break;
            }
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
            return highlightMatch(text, this.filters.search.trim());
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
            const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
            const d = new Date(dateStr);
            return d.toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },

        toggleCollapse() {
            this.collapsed = !this.collapsed;
            window.sidebarPreference.save('notes', this.collapsed);
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
            this.teardownViewerPanel();
            if (this._notesObserver) {
                this._notesObserver.disconnect();
                this._notesObserver = null;
            }
        },
    };
};
