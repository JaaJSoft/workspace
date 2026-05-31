// "[[" note-link popup for the Milkdown Crepe editor.
// Built on @milkdown/plugin-slash's SlashProvider with a "[[" trigger.
import { slashFactory, SlashProvider } from 'https://esm.sh/@milkdown/plugin-slash@7.17.3';
import { matchTrigger, replacementRange } from './wikilink_match.js';

const slash = slashFactory('wikilink');

// Returns an applier: call it with the Crepe editor to register the plugin.
//   createWikilinkSlash({ search })(crepe.editor)
export function createWikilinkSlash({ search } = {}) {
  const menu = document.createElement('div');
  menu.className = 'wikilink-menu';
  menu.setAttribute('data-testid', 'wikilink-menu');
  // Start hidden. SlashProvider only writes data-show on its first editor
  // update, so without this the empty popup flashes at the top-left corner
  // until the user starts typing. It flips this to 'true' when "[[" triggers.
  menu.dataset.show = 'false';

  let provider;
  let view = null;
  let items = [];
  let activeIndex = 0;
  let generation = 0;

  function clearMenu() {
    while (menu.firstChild) menu.removeChild(menu.firstChild);
  }

  // Build rows with DOM methods + textContent so note names/paths are never
  // interpreted as HTML (no innerHTML on user-controlled values -> no XSS).
  function setMessage(cls, text) {
    clearMenu();
    const div = document.createElement('div');
    div.className = cls;
    div.textContent = text;
    menu.appendChild(div);
  }

  function render() {
    if (!items.length) {
      setMessage('wikilink-empty', 'No notes found');
      return;
    }
    clearMenu();
    items.forEach(function(it, i) {
      const row = document.createElement('div');
      row.className = 'wikilink-item' + (i === activeIndex ? ' is-active' : '');
      row.setAttribute('data-testid', 'wikilink-item');
      row.dataset.i = String(i);
      if (it.path) row.title = it.path;
      row.textContent = it.name;
      menu.appendChild(row);
    });
  }

  async function runSearch(query) {
    const g = ++generation;
    let results = [];
    try {
      results = await search(query);
    } catch (e) {
      if (g === generation) {
        items = [];  // drop stale results so Arrow/Enter can't resurrect them
        setMessage('wikilink-error', "Couldn't load notes");
      }
      return;
    }
    if (g !== generation) return;  // a newer query already ran; drop this result
    items = results || [];
    activeIndex = 0;
    render();
  }

  function pick(item) {
    if (!item || !view) return;
    const state = view.state;
    const m = matchTrigger(provider.getContent(view) || '');
    if (!m) { provider.hide(); return; }
    const caret = state.selection.from;
    const range = replacementRange(caret, m.length);
    const linkMark = state.schema.marks.link;
    const href = '/notes?file=' + item.uuid;
    const node = state.schema.text(
      item.name,
      linkMark ? [linkMark.create({ href: href })] : []
    );
    view.dispatch(state.tr.replaceWith(range.from, range.to, node));
    provider.hide();
    view.focus();
  }

  menu.addEventListener('mousedown', function(e) {
    // mousedown (not click) so the editor doesn't lose the selection first.
    e.preventDefault();
    const el = e.target.closest('[data-i]');
    if (el) pick(items[Number(el.dataset.i)]);
  });

  // Self-gates by re-checking the live trigger, so no visibility flag is
  // needed. Capture phase + stopPropagation keep ProseMirror from also acting
  // on Enter/Arrows while the popup owns them.
  function onKeydown(e) {
    if (!provider || !view) return;
    const active = matchTrigger(provider.getContent(view) || '');
    if (!active) return;                       // popup not open
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      provider.hide();
      // provider.hide() only repaints on the next editor update (which Escape
      // does not trigger), so force the dismissed state now.
      menu.dataset.show = 'false';
      return;
    }
    if (!items.length) return;                 // nothing to navigate or pick
    if (e.key === 'ArrowDown') {
      e.preventDefault(); e.stopPropagation();
      activeIndex = (activeIndex + 1) % items.length;
      render();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation();
      activeIndex = (activeIndex - 1 + items.length) % items.length;
      render();
    } else if (e.key === 'Enter') {
      e.preventDefault(); e.stopPropagation();
      pick(items[activeIndex]);
    }
  }

  function pluginView(v) {
    view = v;
    v.dom.addEventListener('keydown', onKeydown, true);
    provider = new SlashProvider({
      content: menu,
      trigger: '[',  // the 2nd "[" keypress opens it; the full "[[" match is enforced in shouldShow
      shouldShow(innerView) {
        if (!innerView.editable) return false;
        const m = matchTrigger(provider.getContent(innerView) || '');
        if (!m) return false;
        runSearch(m.query);
        return true;
      },
    });
    return {
      update: (updatedView, prevState) => { view = updatedView; provider.update(updatedView, prevState); },
      destroy: () => {
        v.dom.removeEventListener('keydown', onKeydown, true);
        provider.destroy();
        menu.remove();
      },
    };
  }

  return (editor) =>
    editor
      .config((ctx) => { ctx.set(slash.key, { view: pluginView }); })
      .use(slash);
}
