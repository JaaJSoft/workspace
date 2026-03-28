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
    var titleMap = { all: 'All notes', favorites: 'Favorites', recent: 'Recent', journal: 'Journal' };

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

        async init() {
            this.collapsed = localStorage.getItem('notes-sidebar-collapsed') === 'true';

            // Load folder data from embedded JSON
            this._loadFolderData();

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

            // Sync reactive prefs and re-sort when sort preference changes
            window.addEventListener('notes:preferences-changed', function(e) {
                var oldSort = this.notePrefs.sortBy;
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
                var note = this.notes.find(function(n) { return n.uuid === config.file; });
                if (note) {
                    await this.selectNote(note);
                } else {
                    await this.selectNoteById(config.file);
                }
            }

            // Handle browser back/forward on mobile
            window.addEventListener('popstate', function() {
                var p = new URLSearchParams(window.location.search);
                var fileId = p.get('file');
                if (fileId) {
                    this.selectNoteById(fileId);
                } else {
                    this.selectedNote = null;
                }
            }.bind(this));
        },

        // ── Folder data management ──────────────────────────

        _loadFolderData() {
            var el = document.getElementById('notes-folders-data');
            if (el) {
                try { this.sidebarFolders = JSON.parse(el.textContent); }
                catch (e) { this.sidebarFolders = []; }
            }
            var gel = document.getElementById('notes-group-folders-data');
            if (gel) {
                try { this.sidebarGroupFolders = JSON.parse(gel.textContent); }
                catch (e) { this.sidebarGroupFolders = []; }
            }
            // Initialize depth/ancestors for root folders
            [this.sidebarFolders, this.sidebarGroupFolders].forEach(function(list) {
                list.forEach(function(f) {
                    if (f.depth === undefined) f.depth = 0;
                    if (f.ancestors === undefined) f.ancestors = '';
                });
            });
        },

        isLoadingChildren(uuid) {
            return this.loadingChildren.indexOf(uuid) !== -1;
        },

        async _loadChildren(uuid, folderList) {
            if (this._loadedChildren[uuid]) return;
            this._loadedChildren[uuid] = true;
            this.loadingChildren.push(uuid);

            var resp = await fetch('/api/v1/files?parent=' + uuid + '&node_type=folder&ordering=name');
            if (!resp.ok) {
                this.loadingChildren = this.loadingChildren.filter(function(id) { return id !== uuid; });
                return;
            }

            var children = await resp.json();
            if (children.length === 0) {
                this.loadingChildren = this.loadingChildren.filter(function(id) { return id !== uuid; });
                return;
            }

            // Find parent in the list to compute depth/ancestors
            var parentIdx = -1;
            var parentFolder = null;
            for (var i = 0; i < folderList.length; i++) {
                if (folderList[i].uuid === uuid) {
                    parentIdx = i;
                    parentFolder = folderList[i];
                    break;
                }
            }
            if (parentIdx === -1) return;

            var childDepth = (parentFolder.depth || 0) + 1;
            var childAncestors = parentFolder.ancestors
                ? parentFolder.ancestors + ',' + uuid
                : uuid;

            children.forEach(function(c) {
                c.depth = childDepth;
                c.ancestors = childAncestors;
            });

            // Insert children after parent (and after any existing children of parent)
            var insertIdx = parentIdx + 1;
            while (insertIdx < folderList.length &&
                   folderList[insertIdx].depth > parentFolder.depth) {
                insertIdx++;
            }
            folderList.splice.apply(folderList, [insertIdx, 0].concat(children));
            this.loadingChildren = this.loadingChildren.filter(function(id) { return id !== uuid; });

            // Refresh icons for new elements
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        // ── Sidebar navigation ──────────────────────────────

        _sortParam() {
            var sortMap = { name: 'name', modified: '-updated_at', created: '-created_at' };
            return sortMap[window._notesPrefsCache.sortBy] || '-updated_at';
        },

        async setView(view, id, name, skipUrl) {
            id = id || null;
            var viewChanged = (view !== this.activeView || id !== this.activeId);
            this.activeView = view;
            this.activeId = id;
            this._closeDrawerOnMobile();

            if (view === 'all') {
                this.viewTitle = 'All notes';
            } else if (view === 'favorites') {
                this.viewTitle = 'Favorites';
            } else if (view === 'recent') {
                this.viewTitle = 'Recent';
            } else if (view === 'tag') {
                if (!name && id) {
                    var tagEl = document.querySelector('[data-tag-uuid="' + id + '"]');
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
            this._closeDrawerOnMobile();

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
            this.updateUrl({push: this.isMobile()});
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
                container.textContent = err.message;
            } finally {
                if (generation === this._loadGeneration) {
                    this.loadingEditor = false;
                }
            }
        },

        // ── Note CRUD ───────────────────────────────────────

        async createNote() {
            var name = await AppDialog.prompt({
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

            var parentUuid = (this.activeView === 'folder' || this.activeView === 'journal' || this.activeView === 'group_folder') ? this.activeId : null;
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
                var ok = await AppDialog.confirm({
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

        async toggleFavorite(note) {
            if (!note || this.togglingFavorite) return;
            this.togglingFavorite = true;
            var isFav = note.is_favorite;
            var resp = await fetch('/api/v1/files/' + note.uuid + '/favorite', {
                method: isFav ? 'DELETE' : 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });
            if (resp.ok) {
                note.is_favorite = !isFav;
                if (this.activeView === 'favorites' && isFav) {
                    var idx = this.notes.findIndex(function(n) { return n.uuid === note.uuid; });
                    this.notes = this.notes.filter(function(n) { return n.uuid !== note.uuid; });
                    if (this.selectedNote && this.selectedNote.uuid === note.uuid) {
                        var next = this.notes[idx] || this.notes[idx - 1] || null;
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

        createGroupFolder: function(groupId, groupName) {
            var self = this;
            fetch('/api/v1/files', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({
                    name: groupName,
                    node_type: 'folder',
                    group: groupId,
                }),
            }).then(function(resp) {
                if (resp.ok) {
                    self.refreshSidebar();
                }
            }).catch(function() {});
        },

        // ── Context menu ─────────────────────────────────────

        openCtxMenu(e, type, data) {
            e.preventDefault();
            var x = e.clientX;
            var y = e.clientY;
            // Prevent overflow
            var menuW = 220, menuH = 200;
            if (x + menuW > window.innerWidth) x = window.innerWidth - menuW;
            if (y + menuH > window.innerHeight) y = window.innerHeight - menuH;
            this.ctxMenu = { open: true, x: x, y: y, type: type, data: data, actions: null };

            // Fetch dynamic actions for folder types
            if (type === 'folder' || type === 'group_folder') {
                this._fetchFolderActions(data.uuid);
            }
        },

        async _fetchFolderActions(uuid) {
            try {
                var resp = await fetch('/api/v1/files/actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ uuids: [uuid] }),
                });
                if (resp.ok) {
                    var data = await resp.json();
                    var allActions = data[uuid] || [];
                    // Filter to relevant folder actions for the notes sidebar
                    var relevant = ['rename', 'delete'];
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

        closeCtxMenu() {
            this.ctxMenu.open = false;
        },

        ctxFolderAction(action) {
            var m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            if (action.id === 'rename') {
                this.showRenameDialog(m.data.uuid, m.data.name);
            } else if (action.id === 'delete') {
                if (!confirm('Delete folder "' + m.data.name + '"? Notes inside will be moved to trash.')) return;
                var self = this;
                fetch('/api/v1/files/' + m.data.uuid, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCSRFToken() },
                }).then(function(resp) {
                    if (resp.ok) self.refreshSidebar();
                });
            }
        },

        ctxAction(action) {
            var m = this.ctxMenu;
            this.closeCtxMenu();
            if (!m.data) return;

            if (m.type === 'tag') {
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

        isTreeHidden(uuid, ancestorsStr) {
            var hidden = this.notePrefs.hiddenItems || [];
            if (hidden.indexOf(uuid) !== -1) return true;
            if (!ancestorsStr) return false;
            var ancestors = ancestorsStr.split(',');
            for (var i = 0; i < ancestors.length; i++) {
                if (hidden.indexOf(ancestors[i]) !== -1) return true;
            }
            return false;
        },

        // ── Expanded folders (URL-based) ────────────────────

        expandedFolders: [],

        _readExpandedFromUrl() {
            var p = new URLSearchParams(window.location.search);
            var raw = p.get('expanded');
            return raw ? raw.split(',').filter(Boolean) : [];
        },

        _writeExpandedToUrl() {
            var url = new URL(window.location);
            if (this.expandedFolders.length > 0) {
                url.searchParams.set('expanded', this.expandedFolders.join(','));
            } else {
                url.searchParams.delete('expanded');
            }
            window.history.replaceState({}, '', url);
        },

        async _restoreExpandedFolders() {
            var uuids = this._readExpandedFromUrl();
            if (uuids.length === 0) return;
            this.expandedFolders = uuids;
            // Lazy-load children for each expanded folder in order
            for (var i = 0; i < uuids.length; i++) {
                var folderList = this._findFolderList(uuids[i]);
                if (folderList) {
                    await this._loadChildren(uuids[i], folderList);
                }
            }
            this.$nextTick(function() {
                if (window.lucide) window.lucide.createIcons();
            });
        },

        isFolderExpanded(uuid) {
            return this.expandedFolders.indexOf(uuid) !== -1;
        },

        async toggleFolderExpand(uuid) {
            var idx = this.expandedFolders.indexOf(uuid);
            if (idx === -1) {
                // Expanding: lazy-load children first
                var folderList = this._findFolderList(uuid);
                if (folderList) {
                    await this._loadChildren(uuid, folderList);
                }
                this.expandedFolders = this.expandedFolders.concat([uuid]);
            } else {
                // Collapsing: also remove any expanded descendants
                var toRemove = [uuid];
                var allFolders = this.sidebarFolders.concat(this.sidebarGroupFolders);
                allFolders.forEach(function(f) {
                    if (f.ancestors && f.ancestors.split(',').indexOf(uuid) !== -1) {
                        toRemove.push(f.uuid);
                    }
                });
                this.expandedFolders = this.expandedFolders.filter(function(id) {
                    return toRemove.indexOf(id) === -1;
                });
            }
            this._writeExpandedToUrl();
        },

        _findFolderList(uuid) {
            for (var i = 0; i < this.sidebarFolders.length; i++) {
                if (this.sidebarFolders[i].uuid === uuid) return this.sidebarFolders;
            }
            for (var j = 0; j < this.sidebarGroupFolders.length; j++) {
                if (this.sidebarGroupFolders[j].uuid === uuid) return this.sidebarGroupFolders;
            }
            return null;
        },

        isAncestorCollapsed(ancestorsStr) {
            if (!ancestorsStr) return false;
            var ancestors = ancestorsStr.split(',');
            for (var i = 0; i < ancestors.length; i++) {
                if (this.expandedFolders.indexOf(ancestors[i]) === -1) return true;
            }
            return false;
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

        updateUrl(options) {
            options = options || {};
            var push = options.push || false;
            var url = new URL(window.location);
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
            var resp = await fetch('/notes', {
                headers: { 'X-Alpine-Request': 'true' },
            });
            if (resp.ok) {
                var html = await resp.text();
                var container = document.getElementById('notes-sidebar');
                if (container) {
                    container.textContent = '';
                    // Parse and insert safely
                    var temp = document.createElement('template');
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
            var sort = '&ordering=' + this._sortParam();
            var base = '/api/v1/files?mime_type=text/markdown';
            var hasSearch = this.filters.search.trim();

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
                var filterTags = this.filters.tags;
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
            var idx = this.filters.tags.indexOf(tagUuid);
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
            var escaped = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            var q = this.filters.search.trim();
            if (!q) return escaped;
            var re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
            return escaped.replace(re, '<mark class="bg-warning/40 text-inherit rounded-sm px-0.5">$1</mark>');
        },

        selectedTagNames() {
            var selected = this.filters.tags;
            if (!selected.length) return '';
            var names = this.allTags
                .filter(function(t) { return selected.indexOf(t.uuid) !== -1; })
                .map(function(t) { return t.name; });
            if (names.length <= 2) return names.join(', ');
            return names.slice(0, 2).join(', ') + ' +' + (names.length - 2);
        },

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

        _closeDrawerOnMobile() {
            if (this.isMobile()) {
                var toggle = document.getElementById('notes-drawer');
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
