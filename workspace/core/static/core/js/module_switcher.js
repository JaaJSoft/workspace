// Navbar module switcher: Alt+M opens it, arrows and letters move across its
// tile grid. The tiles are plain links, so Enter needs no handling.

const MODULE_SWITCHER_COLUMNS = 4;

window.moduleSwitcherNav = {
  // Index of the tile to focus after `key`, or null when the key moves
  // nothing. `index` is the focused tile, -1 when the trigger has focus.
  nextIndex(index, key, count, columns) {
    if (count === 0) return null;
    if (index < 0) {
      return key === 'ArrowDown' || key === 'ArrowRight' ? 0 : null;
    }
    switch (key) {
      case 'ArrowRight':
        return (index + 1) % count;
      case 'ArrowLeft':
        return (index - 1 + count) % count;
      case 'ArrowDown':
        return index + columns < count ? index + columns : null;
      case 'ArrowUp':
        return index - columns >= 0 ? index - columns : null;
      case 'Home':
        return 0;
      case 'End':
        return count - 1;
      default:
        return null;
    }
  },

  // Next tile after `from` whose name starts with `letter`, wrapping around;
  // null when none does.
  letterIndex(names, letter, from) {
    const wanted = letter.toLowerCase();
    for (let step = 1; step <= names.length; step++) {
      const i = (from + step) % names.length;
      if (names[i].trim().toLowerCase().startsWith(wanted)) return i;
    }
    return null;
  },
};

window.moduleSwitcher = function moduleSwitcher() {
  return {
    tiles() {
      return Array.from(this.$el.querySelectorAll('#module-switcher-grid a'));
    },

    // The switcher is a focus-driven dropdown: focusing the trigger opens it.
    open() {
      this.$el.querySelector('label').focus();
    },

    isShortcut(event) {
      // event.code covers macOS, where Alt+M yields "µ" as event.key.
      return event.altKey && (event.key.toLowerCase() === 'm' || event.code === 'KeyM');
    },

    onKeydown(event) {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const tiles = this.tiles();
      const index = tiles.indexOf(document.activeElement);
      const onTrigger = document.activeElement === this.$el.querySelector('label');
      if (index < 0 && !onTrigger) return;

      let target = window.moduleSwitcherNav.nextIndex(index, event.key, tiles.length, MODULE_SWITCHER_COLUMNS);
      if (target === null && /^[a-z]$/i.test(event.key)) {
        const names = tiles.map((tile) => tile.textContent);
        target = window.moduleSwitcherNav.letterIndex(names, event.key, index);
      }
      if (target === null) return;
      event.preventDefault();
      tiles[target].focus();
    },
  };
};
