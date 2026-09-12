/**
 * Tags mixin for Alpine.js components.
 *
 * Provides tag CRUD, assignment, a modal for create/edit (like mail labels)
 * and the tag manager dialog (search, favourites, merge, purge).
 * The consuming component must expose a `selectedNote` or `selectedFile` property
 * representing the currently active file object (with a `.tags` array and `.uuid`),
 * and the navigation contract the shared tag partials call:
 *   tagViewHref(tag)     URL of the tag's view in this module
 *   openTagView(tag)     navigate to it
 *   isTagViewActive(tag) whether it is the view on screen
 *
 * Usage:
 *   return { ...window.tagsMixin(), ... };
 *   // then call this.loadTags() in init().
 */
window.tagsMixin = function tagsMixin() {
    return {
        allTags: [],

        // Modal state (mirrors mail labelModal pattern)
        tagModal: {
            uuid: null,
            name: '',
            icon: '',
            color: '',
            saving: false,
            error: '',
        },

        // Manager dialog state. mergeSource is the tag being merged away,
        // mergeTarget the uuid it merges into.
        tagManager: {
            query: '',
            mergeSource: null,
            mergeTarget: '',
            busy: false,
            error: '',
        },

        async loadTags() {
            const resp = await fetch('/api/v1/tags');
            if (resp.ok) {
                this.allTags = await resp.json();
            }
        },

        // ── Collection views (methods, not getters: this object is spread) ──

        _byName(a, b) {
            return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
        },

        favoriteTags() {
            return this.allTags.filter((t) => t.is_favorite).sort(this._byName);
        },

        filteredManagedTags() {
            const query = this.tagManager.query.trim().toLowerCase();
            return this.allTags
                .filter((t) => !query || t.name.toLowerCase().includes(query))
                .sort((a, b) => (b.is_favorite - a.is_favorite) || this._byName(a, b));
        },

        unusedTagCount() {
            return this.allTags.filter((t) => !t.file_count).length;
        },

        // Every other tag, whatever the manager's search says: the merge
        // dialog opens over the manager and should offer the full list.
        tagMergeTargets() {
            const source = this.tagManager.mergeSource;
            return this.allTags
                .filter((t) => !source || t.uuid !== source.uuid)
                .sort((a, b) => (b.is_favorite - a.is_favorite) || this._byName(a, b));
        },

        _replaceTag(tag) {
            this.allTags = this.allTags.map((t) => (t.uuid === tag.uuid ? tag : t));
        },

        _removeTag(uuid) {
            this.allTags = this.allTags.filter((t) => t.uuid !== uuid);
            const target = this._tagTarget();
            if (target && target.tags) {
                target.tags = target.tags.filter((t) => t.uuid !== uuid);
            }
        },

        _announceTagsChanged() {
            window.dispatchEvent(new CustomEvent('tags-changed'));
        },

        // ── Manager dialog ──────────────────────────────────

        openTagManager() {
            this.tagManager.query = '';
            this.tagManager.error = '';
            // Assignments made since the page loaded moved the counts.
            this.loadTags();
            const dlg = document.getElementById('tag-manager-dialog');
            if (dlg) {
                dlg.showModal();
                setTimeout(() => dlg.querySelector('input[type="search"]')?.focus(), 50);
            }
        },

        closeTagManager() {
            document.getElementById('tag-manager-dialog')?.close();
        },

        // Closes the manager first: the browser stacks modal dialogs, and a
        // tag view opening behind two of them is not a navigation.
        openTagViewFromManager(tag) {
            this.closeTagManager();
            this.openTagView(tag);
        },

        async toggleTagFavorite(tag) {
            const resp = await fetch('/api/v1/tags/' + tag.uuid, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ is_favorite: !tag.is_favorite }),
            });
            if (!resp.ok) {
                this.tagManager.error = 'Failed to update the tag.';
                return;
            }
            this._replaceTag(await resp.json());
            this._announceTagsChanged();
        },

        async deleteTag(tag) {
            if (!confirm('Delete tag "' + tag.name + '"?')) return;
            const resp = await fetch('/api/v1/tags/' + tag.uuid, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });
            if (!(resp.ok || resp.status === 204)) {
                this.tagManager.error = 'Failed to delete the tag.';
                return;
            }
            this._removeTag(tag.uuid);
            this._announceTagsChanged();
        },

        startTagMerge(tag) {
            this.tagManager.mergeSource = tag;
            this.tagManager.mergeTarget = '';
            this.tagManager.error = '';
            document.getElementById('tag-merge-dialog')?.showModal();
        },

        cancelTagMerge() {
            document.getElementById('tag-merge-dialog')?.close();
            this.tagManager.mergeSource = null;
            this.tagManager.mergeTarget = '';
        },

        async confirmTagMerge() {
            const m = this.tagManager;
            if (!m.mergeSource || !m.mergeTarget || m.busy) return;
            m.busy = true;
            m.error = '';
            const resp = await fetch('/api/v1/tags/' + m.mergeSource.uuid + '/merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ into: m.mergeTarget }),
            });
            m.busy = false;
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                m.error = data.into || data.detail || 'Failed to merge the tags.';
                return;
            }
            const merged = await resp.json();
            this._removeTag(m.mergeSource.uuid);
            this._replaceTag(merged);
            this.cancelTagMerge();
            this._announceTagsChanged();
        },

        async purgeUnusedTags() {
            const count = this.unusedTagCount();
            if (!count) return;
            if (!confirm('Delete ' + count + ' unused tag' + (count === 1 ? '' : 's') + '?')) return;
            const resp = await fetch('/api/v1/tags/purge-unused', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });
            if (!resp.ok) {
                this.tagManager.error = 'Failed to delete the unused tags.';
                return;
            }
            this.allTags = this.allTags.filter((t) => t.file_count);
            this._announceTagsChanged();
        },

        _tagTarget() {
            return this.selectedNote || this.selectedFile || null;
        },

        fileHasTag(tagUuid) {
            const target = this._tagTarget();
            if (!target || !target.tags) return false;
            return target.tags.some(function(t) { return t.uuid === tagUuid; });
        },

        async toggleFileTag(tag) {
            const target = this._tagTarget();
            if (!target) return;
            const hasIt = this.fileHasTag(tag.uuid);
            if (hasIt) {
                await fetch('/api/v1/files/' + target.uuid + '/tags/' + tag.uuid, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCSRFToken() },
                });
                target.tags = target.tags.filter(function(t) {
                    return t.uuid !== tag.uuid;
                });
            } else {
                const resp = await fetch('/api/v1/files/' + target.uuid + '/tags', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCSRFToken(),
                    },
                    body: JSON.stringify({ tag: tag.uuid }),
                });
                if (resp.ok) {
                    target.tags.push({ uuid: tag.uuid, name: tag.name, icon: tag.icon, color: tag.color });
                }
            }
        },

        // ── Tag modal (create / edit / delete) ───────────────

        showTagModal(tag) {
            this.tagModal = {
                uuid: tag ? tag.uuid : null,
                name: tag ? tag.name : '',
                icon: tag ? (tag.icon || '') : '',
                color: tag ? (tag.color || '') : '',
                saving: false,
                error: '',
            };
            const dlg = document.getElementById('tag-dialog');
            if (dlg) {
                dlg.showModal();
                setTimeout(function() {
                    const input = dlg.querySelector('input[type="text"]');
                    if (input) input.focus();
                }, 50);
            }
        },

        closeTagModal() {
            const dlg = document.getElementById('tag-dialog');
            if (dlg) dlg.close();
        },

        async saveTagModal() {
            const m = this.tagModal;
            if (!m.name.trim()) return;
            m.saving = true;
            m.error = '';

            const isEdit = !!m.uuid;
            const url = isEdit ? '/api/v1/tags/' + m.uuid : '/api/v1/tags';
            const resp = await fetch(url, {
                method: isEdit ? 'PATCH' : 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ name: m.name.trim(), icon: m.icon, color: m.color }),
            });

            if (!resp.ok) {
                const data = await resp.json().catch(function() { return {}; });
                m.error = data.name ? data.name[0] : (data.detail || 'Failed to save tag.');
                m.saving = false;
                return;
            }

            const tag = await resp.json();
            if (isEdit) {
                this._replaceTag(tag);
            } else {
                this.allTags.push(tag);
                // Auto-assign to active file/note
                if (this._tagTarget()) {
                    await this.toggleFileTag(tag);
                }
            }

            m.saving = false;
            this.closeTagModal();
            this._announceTagsChanged();
        },

        async deleteTagConfirm() {
            const m = this.tagModal;
            if (!m.uuid) return;
            const before = this.allTags.length;
            await this.deleteTag({ uuid: m.uuid, name: m.name });
            if (this.allTags.length < before) this.closeTagModal();
        },
    };
};
