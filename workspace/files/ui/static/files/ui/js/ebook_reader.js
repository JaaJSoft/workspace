/**
 * The e-book reader page (files/ui/ebook_reader.html).
 *
 * Opens the book at the page's data-src in a <foliate-view> and drives the
 * toolbar around it. The page's Content-Security-Policy forbids inline code,
 * so everything the page does lives in this file.
 */

const EBOOK_FONT_SCALES = [80, 90, 100, 110, 120, 135, 150, 175, 200];
const EBOOK_DEFAULT_FONT_SCALE = 100;
const EBOOK_FONT_SCALE_KEY = 'files.ebookReader.fontScale';

/** *src* as an absolute URL when it is on *origin*, else null. */
function ebookSourceUrl(src, origin) {
  if (!src) return null;
  let url;
  try {
    url = new URL(src, origin);
  } catch {
    return null;
  }
  return url.origin === origin ? url.href : null;
}

/** The scale one step from *current* in *direction* (+1 or -1), clamped. */
function ebookStepFontScale(current, direction) {
  const index = EBOOK_FONT_SCALES.indexOf(current);
  const from = index === -1 ? EBOOK_FONT_SCALES.indexOf(EBOOK_DEFAULT_FONT_SCALE) : index;
  const to = Math.min(Math.max(from + direction, 0), EBOOK_FONT_SCALES.length - 1);
  return EBOOK_FONT_SCALES[to];
}

/** A stored scale when it is one of the steps, else the default. */
function ebookParseFontScale(value) {
  const scale = Number(value);
  return EBOOK_FONT_SCALES.includes(scale) ? scale : EBOOK_DEFAULT_FONT_SCALE;
}

/**
 * The stylesheet laid over every chapter: the page theme's colours, so the
 * book follows light and dark, and the reader's text size. Not !important: a
 * book that sets its own colours keeps them.
 */
function ebookBookStyles({ colorScheme, color, linkColor, fontScale }) {
  return `
    html {
      color-scheme: ${colorScheme};
      color: ${color};
      font-size: ${fontScale}%;
    }
    a:link, a:visited {
      color: ${linkColor};
    }
    p, li, blockquote, dd {
      line-height: 1.5;
      hyphens: auto;
    }
    pre {
      white-space: pre-wrap !important;
    }
  `;
}

/** The book's metadata title, which may be a language map. */
function ebookTitle(metadata) {
  const title = metadata?.title;
  if (!title) return '';
  if (typeof title === 'string') return title;
  return Object.values(title)[0] || '';
}

window.ebookReader = {
  sourceUrl: ebookSourceUrl,
  stepFontScale: ebookStepFontScale,
  parseFontScale: ebookParseFontScale,
  bookStyles: ebookBookStyles,
  title: ebookTitle,
};

function ebookReadFontScale() {
  try {
    return ebookParseFontScale(localStorage.getItem(EBOOK_FONT_SCALE_KEY));
  } catch {
    return EBOOK_DEFAULT_FONT_SCALE;
  }
}

function ebookSaveFontScale(scale) {
  try {
    localStorage.setItem(EBOOK_FONT_SCALE_KEY, String(scale));
  } catch {
    // A private window or blocked storage: the size lasts for this page only.
  }
}

/** The reader's theme colours, read off the page the daisyUI theme styled. */
function ebookThemeColors(root) {
  const style = getComputedStyle(root);
  const probe = document.createElement('span');
  probe.className = 'text-primary';
  root.append(probe);
  const linkColor = getComputedStyle(probe).color;
  probe.remove();
  return {
    colorScheme: getComputedStyle(document.documentElement).colorScheme || 'light',
    color: style.color,
    linkColor,
  };
}

/**
 * Keep the page on the theme of the page framing it, when there is one: the
 * user switching theme in the app switches the book too.
 */
function ebookFollowParentTheme(onChange) {
  let parentRoot = null;
  try {
    if (window.parent !== window) parentRoot = window.parent.document.documentElement;
  } catch {
    return;
  }
  if (!parentRoot) return;
  const html = document.documentElement;
  const sync = () => {
    const theme = parentRoot.getAttribute('data-theme');
    if (theme && theme !== html.getAttribute('data-theme')) {
      html.setAttribute('data-theme', theme);
      onChange();
    }
  };
  sync();
  new MutationObserver(sync).observe(parentRoot, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
}

function ebookRenderToc(list, items, onSelect) {
  for (const item of items) {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = (item.label || '').trim();
    button.addEventListener('click', () => onSelect(item.href));
    li.append(button);
    if (item.subitems?.length) {
      const sublist = document.createElement('ul');
      ebookRenderToc(sublist, item.subitems, onSelect);
      li.append(sublist);
    }
    list.append(li);
  }
}

async function openEbookReader(root) {
  const ref = (name) => root.querySelector(`[data-ref="${name}"]`);
  const action = (name) => root.querySelector(`[data-action="${name}"]`);
  const toc = ref('toc');
  const tocButton = action('toc');
  const progress = ref('progress');
  const percentFormat = new Intl.NumberFormat(document.documentElement.lang || undefined, {
    style: 'percent',
  });

  const fail = (error) => {
    console.error(error);
    ref('loading').classList.replace('flex', 'hidden');
    ref('error').classList.replace('hidden', 'flex');
  };

  const src = ebookSourceUrl(root.dataset.src, window.location.origin);
  if (!src) {
    fail(new Error('The book is not on this site'));
    return;
  }

  let book;
  let view;
  try {
    const [{ makeBook }, response] = await Promise.all([
      import(root.dataset.foliateUrl),
      fetch(src, { credentials: 'same-origin' }),
    ]);
    if (!response.ok) throw new Error(`The book could not be fetched: ${response.status}`);
    const blob = await response.blob();
    // foliate tells the formats apart by name as well as by content.
    book = await makeBook(new File([blob], root.dataset.name || 'book.epub', { type: blob.type }));
    view = document.createElement('foliate-view');
    ref('book').append(view);
    await view.open(book);
  } catch (error) {
    fail(error);
    return;
  }

  // A stylesheet or an image the book lists but does not hold must not stop
  // the chapter from showing.
  book.transformTarget?.addEventListener('data', ({ detail }) => {
    detail.data = Promise.resolve(detail.data).catch((error) => {
      console.warn(`Failed to load ${detail.name}`, error);
      return '';
    });
  });

  let fontScale = ebookReadFontScale();
  const applyStyles = () => {
    view.renderer.setStyles?.(ebookBookStyles({ ...ebookThemeColors(root), fontScale }));
  };
  view.renderer.setAttribute('flow', 'paginated');
  view.renderer.setAttribute('max-column-count', '2');
  applyStyles();
  ebookFollowParentTheme(applyStyles);

  const title = ebookTitle(book.metadata);
  if (title) {
    ref('title').textContent = title;
    document.title = `${title} - Workspace`;
  }

  const closeToc = () => {
    toc.classList.add('hidden');
    tocButton.setAttribute('aria-expanded', 'false');
  };
  if (book.toc?.length) {
    ebookRenderToc(ref('toc-list'), book.toc, (href) => {
      view.goTo(href);
      closeToc();
    });
  } else {
    ref('toc-empty').classList.remove('hidden');
  }
  tocButton.addEventListener('click', () => {
    const open = !toc.classList.toggle('hidden');
    tocButton.setAttribute('aria-expanded', String(open));
  });

  action('left').addEventListener('click', () => view.goLeft());
  action('right').addEventListener('click', () => view.goRight());
  const setFontScale = (direction) => {
    fontScale = ebookStepFontScale(fontScale, direction);
    ebookSaveFontScale(fontScale);
    applyStyles();
  };
  action('smaller').addEventListener('click', () => setFontScale(-1));
  action('larger').addEventListener('click', () => setFontScale(1));

  progress.dir = book.dir || 'ltr';
  progress.addEventListener('change', () => view.goToFraction(parseFloat(progress.value)));

  const onKeydown = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target instanceof Element && event.target.closest('input, button')) return;
    if (event.key === 'ArrowLeft') view.goLeft();
    else if (event.key === 'ArrowRight') view.goRight();
    else if (event.key === 'PageUp') view.prev();
    else if (event.key === 'PageDown' || event.key === ' ') view.next();
    else if (event.key === 'Escape') closeToc();
    else return;
    event.preventDefault();
  };
  document.addEventListener('keydown', onKeydown);
  // Each chapter is a document of its own: a key pressed while it has the
  // focus never reaches the page's.
  view.addEventListener('load', ({ detail: { doc } }) => {
    doc.addEventListener('keydown', onKeydown);
  });

  view.addEventListener('relocate', ({ detail }) => {
    const fraction = detail.fraction ?? 0;
    progress.value = String(fraction);
    ref('percent').textContent = percentFormat.format(fraction);
    ref('chapter').textContent = (detail.tocItem?.label || '').trim();
  });

  await view.renderer.next();
  ref('loading').classList.replace('flex', 'hidden');
  for (const control of root.querySelectorAll('[data-action], [data-ref="progress"]')) {
    control.disabled = false;
  }
  root.dataset.ready = '';
}

document.addEventListener('DOMContentLoaded', () => {
  window.LucideUtils?.init();
  const root = document.getElementById('ebook-reader');
  if (root) openEbookReader(root);
});
