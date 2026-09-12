// The Files sidebar's Tags section. The <aside> is not inside fileBrowser(),
// so the section keeps its own copy of the tag list (re-fetched on every
// tags-changed) and asks the browser component, which owns the tag dialogs,
// to open them through window events.
window.tagSidebar = function tagSidebar() {
  return {
    ...window.tagsMixin(),

    init() {
      this.loadTags();
      window.addEventListener('tags-changed', () => this.loadTags());
    },

    openTagManager() {
      window.dispatchEvent(new CustomEvent('open-tag-manager'));
    },

    showTagModal() {
      window.dispatchEvent(new CustomEvent('open-tag-dialog'));
    },

    tagViewHref(tag) {
      return '/files?tag=' + tag.uuid;
    },

    openTagView(tag) {
      window.folderNav.navigateTo(this.tagViewHref(tag));
    },

    // activeView comes from the enclosing sidebarCollapse() scope.
    isTagViewActive(tag) {
      return this.activeView === 'tag:' + tag.uuid;
    },
  };
};
