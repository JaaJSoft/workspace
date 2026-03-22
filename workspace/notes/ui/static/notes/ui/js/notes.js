function getCSRFToken() {
    return document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
}

window.notesApp = function notesApp(config) {
    config = config || {};
    var initialView = config.view || 'all';
    var titleMap = { all: 'All notes', recent: 'Recent', journal: 'Journal' };

    return {
        // Sidebar
        collapsed: false,
        activeView: initialView,
        activeId: config.id || null,
        viewTitle: titleMap[initialView] || 'All notes',

        // Note list
        notes: [],
        loadingNotes: false,

        // Tags (for editor tag dropdown)
        allTags: [],

        // Editor
        selectedNote: null,
        loadingEditor: false,
        _loadedScripts: [],
        _loadGeneration: 0,

        async init() {
            this.collapsed = localStorage.getItem('notes-sidebar-collapsed') === 'true';

            // Listen for sidebar refresh events
            window.addEventListener('notes:refresh-sidebar', this.refreshSidebar.bind(this));

            // Load tags for the editor dropdown
            var resp = await fetch('/api/v1/tags');
            if (resp.ok) {
                this.allTags = await resp.json();
            }

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

        async setView(view, id, name, skipUrl) {
            this.activeView = view;
            this.activeId = id || null;

            var url;
            if (view === 'all') {
                this.viewTitle = 'All notes';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200';
            } else if (view === 'recent') {
                this.viewTitle = 'Recent';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=50';
            } else if (view === 'tag') {
                if (!name && id) {
                    var tagEl = document.querySelector('[data-tag-uuid="' + id + '"]');
                    if (tagEl) name = tagEl.dataset.tagName;
                }
                this.viewTitle = name || 'Tag';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200&tags=' + id;
            } else if (view === 'folder') {
                if (!name && id) {
                    var folderEl = document.querySelector('[data-folder-uuid="' + id + '"]');
                    if (folderEl) name = folderEl.dataset.folderName;
                }
                this.viewTitle = name || 'Folder';
                url = '/api/v1/files?mime_type=text/markdown&parent=' + id;
            } else {
                this.viewTitle = 'All notes';
                this.activeView = 'all';
                url = '/api/v1/files?mime_type=text/markdown&recent=1&recent_limit=200';
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
            if (!confirm('Delete "' + this.noteName(this.selectedNote) + '"?')) return;

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

        // ── Tags on notes ───────────────────────────────────

        noteHasTag(tagUuid) {
            if (!this.selectedNote || !this.selectedNote.tags) return false;
            return this.selectedNote.tags.some(function(t) { return t.uuid === tagUuid; });
        },

        async toggleNoteTag(tag) {
            if (!this.selectedNote) return;
            var hasIt = this.noteHasTag(tag.uuid);
            if (hasIt) {
                await fetch('/api/v1/files/' + this.selectedNote.uuid + '/tags/' + tag.uuid, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCSRFToken() },
                });
                this.selectedNote.tags = this.selectedNote.tags.filter(function(t) {
                    return t.uuid !== tag.uuid;
                });
            } else {
                var resp = await fetch('/api/v1/files/' + this.selectedNote.uuid + '/tags', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ tag: tag.uuid }),
                });
                if (resp.ok) {
                    this.selectedNote.tags.push({ uuid: tag.uuid, name: tag.name, color: tag.color });
                }
            }
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
