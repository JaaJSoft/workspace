/**
 * Tags mixin for Alpine.js components.
 *
 * Provides tag CRUD, assignment, and a modal for create/edit (like mail labels).
 * The consuming component must expose a `selectedNote` or `selectedFile` property
 * representing the currently active file object (with a `.tags` array and `.uuid`).
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
            color: 'ghost',
            saving: false,
            error: '',
        },

        async loadTags() {
            var resp = await fetch('/api/v1/tags');
            if (resp.ok) {
                this.allTags = await resp.json();
            }
        },

        _tagTarget() {
            return this.selectedNote || this.selectedFile || null;
        },

        fileHasTag(tagUuid) {
            var target = this._tagTarget();
            if (!target || !target.tags) return false;
            return target.tags.some(function(t) { return t.uuid === tagUuid; });
        },

        async toggleFileTag(tag) {
            var target = this._tagTarget();
            if (!target) return;
            var hasIt = this.fileHasTag(tag.uuid);
            if (hasIt) {
                await fetch('/api/v1/files/' + target.uuid + '/tags/' + tag.uuid, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCSRFToken() },
                });
                target.tags = target.tags.filter(function(t) {
                    return t.uuid !== tag.uuid;
                });
            } else {
                var resp = await fetch('/api/v1/files/' + target.uuid + '/tags', {
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
                color: tag ? (tag.color || 'ghost') : 'ghost',
                saving: false,
                error: '',
            };
            var dlg = document.getElementById('tag-dialog');
            if (dlg) {
                dlg.showModal();
                var self = this;
                setTimeout(function() {
                    var input = dlg.querySelector('input[type="text"]');
                    if (input) input.focus();
                }, 50);
            }
        },

        closeTagModal() {
            var dlg = document.getElementById('tag-dialog');
            if (dlg) dlg.close();
        },

        async saveTagModal() {
            var m = this.tagModal;
            if (!m.name.trim()) return;
            m.saving = true;
            m.error = '';

            var isEdit = !!m.uuid;
            var url = isEdit ? '/api/v1/tags/' + m.uuid : '/api/v1/tags';
            var resp = await fetch(url, {
                method: isEdit ? 'PATCH' : 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ name: m.name.trim(), icon: m.icon, color: m.color }),
            });

            if (!resp.ok) {
                var data = await resp.json().catch(function() { return {}; });
                m.error = data.name ? data.name[0] : (data.detail || 'Failed to save tag.');
                m.saving = false;
                return;
            }

            var tag = await resp.json();
            if (isEdit) {
                // Update in allTags
                for (var i = 0; i < this.allTags.length; i++) {
                    if (this.allTags[i].uuid === tag.uuid) {
                        this.allTags[i] = tag;
                        break;
                    }
                }
            } else {
                this.allTags.push(tag);
                // Auto-assign to active file/note
                if (this._tagTarget()) {
                    await this.toggleFileTag(tag);
                }
            }

            m.saving = false;
            this.closeTagModal();
            window.dispatchEvent(new CustomEvent('notes:refresh-sidebar'));
        },

        async deleteTagConfirm() {
            var m = this.tagModal;
            if (!m.uuid) return;
            if (!confirm('Delete tag "' + m.name + '"?')) return;

            var resp = await fetch('/api/v1/tags/' + m.uuid, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCSRFToken() },
            });

            if (resp.ok || resp.status === 204) {
                this.allTags = this.allTags.filter(function(t) { return t.uuid !== m.uuid; });
                // Remove from selected file's tags too
                var target = this._tagTarget();
                if (target && target.tags) {
                    var uuid = m.uuid;
                    target.tags = target.tags.filter(function(t) { return t.uuid !== uuid; });
                }
                this.closeTagModal();
                window.dispatchEvent(new CustomEvent('notes:refresh-sidebar'));
            }
        },
    };
};
