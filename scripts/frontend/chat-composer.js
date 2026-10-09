// Rich-text chat composer: a minimal Milkdown editor (commonmark + gfm, no
// Crepe UI) that shows formatting as it will be sent and serializes back to
// the markdown the server renders.
//
// The chat page owns the message text as a markdown string; this module only
// turns it into an editable document and back, and exposes the handful of
// operations the composer needs (marks, mentions, emoji, links).
import {
  Editor,
  defaultValueCtx,
  editorViewCtx,
  editorViewOptionsCtx,
  parserCtx,
  remarkStringifyOptionsCtx,
  rootCtx,
  serializerCtx,
} from '@milkdown/kit/core';
import { clipboard } from '@milkdown/kit/plugin/clipboard';
import { history } from '@milkdown/kit/plugin/history';
import { commonmark, hardbreakSchema, inlineCodeSchema } from '@milkdown/kit/preset/commonmark';
import { gfm, strikethroughInputRule, strikethroughSchema } from '@milkdown/kit/preset/gfm';
import { markRule } from '@milkdown/kit/prose';
import { chainCommands, newlineInCode, toggleMark } from '@milkdown/kit/prose/commands';
import { keymap } from '@milkdown/kit/prose/keymap';
import { liftListItem, splitListItem } from '@milkdown/kit/prose/schema-list';
import { Plugin, TextSelection } from '@milkdown/kit/prose/state';
import { $inputRule, $prose } from '@milkdown/kit/utils';

const MARKS = {
  bold: 'strong',
  italic: 'emphasis',
  strike: 'strike_through',
  code: 'inlineCode',
  link: 'link',
};

// The server renders with hard_wrap, so a bare newline already is a line
// break. Milkdown would write a hard break as `\` + newline (CommonMark's
// explicit form), which reads as a stray backslash to anyone who later edits
// the message as markdown - so every break is written, and shown, as a plain
// newline.
function plainNewlineBreaks(ctx) {
  ctx.update(hardbreakSchema.key, (prev) => (innerCtx) => ({
    ...prev(innerCtx),
    toDOM: () => ['br'],
    toMarkdown: {
      match: (node) => node.type.name === 'hardbreak',
      runner: (state) => state.addNode('text', undefined, '\n'),
    },
  }));
}

// The markdown is not only rendered: it is what the reply preview, the
// notifications, the search index and the markdown mode show. The stock
// serializer escapes every character that could ever start a construct
// (`snake\_case`, `https\://`, `5 \* 3`), which turns plain prose into
// backslash soup. Text is written with the escapes that change how the server
// renders it, and no others.
const URL_TOKEN = /\b(?:https?:\/\/|www\.)[^\s<]+/g;
const WORD = /[\p{L}\p{N}]/u;

function relaxEscapes(escaped) {
  let out = '';
  for (let i = 0; i < escaped.length; i++) {
    const ch = escaped[i];
    const next = escaped[i + 1];
    if (ch !== '\\' || next === undefined) {
      out += ch;
      continue;
    }
    const before = out.slice(-1);
    const after = escaped[i + 2] || '';
    let keep = true;
    // Only there to stop autolinking - the server autolinks URLs on purpose.
    if (next === ':') keep = false;
    // An underscore inside a word never opens emphasis.
    else if (next === '_' && WORD.test(before) && WORD.test(after)) keep = false;
    // A lone asterisk between spaces is arithmetic, not emphasis. Not at
    // the start of a line, where `* ` opens a list.
    else if (next === '*' && before === ' ' && (after === ' ' || after === '')) keep = false;
    // A single tilde is not strikethrough; `~~` is.
    else if (next === '~' && before !== '~' && after !== '~') keep = false;
    // `&` only needs escaping when it would read as an entity (`&amp;`).
    else if (next === '&' && !/^#?[A-Za-z0-9]+;/.test(escaped.slice(i + 2))) keep = false;
    // A bracket only matters when it could open a link: `[x](...)`,
    // `[x][ref]`, `[x]: def`. A bracket closed later in this text by a `]`
    // that opens none of those is literal; one whose `]` lies beyond this text
    // node cannot be judged from here and stays escaped.
    else if (next === '[' && /^[^\]]*\][^([:]/.test(escaped.slice(i + 2))) keep = false;
    else if (next === '(' && before !== ']') keep = false;
    // `<` only matters when it could open a tag or an autolink.
    else if (next === '<' && !/[A-Za-z/!?]/.test(after)) keep = false;
    if (keep) out += ch;
    out += next;
    i++;
  }
  return out;
}

function writeText(node, _parent, state, info) {
  const value = node.value.replace(/\u00a0/g, ' ');
  let out = '';
  let last = 0;
  const writeProse = (from, to) => {
    if (from >= to) return;
    const before = from === 0 ? info.before : value[from - 1];
    const after = to === value.length ? info.after : value[to];
    out += relaxEscapes(state.safe(value.slice(from, to), { ...info, before, after }));
  };
  // URLs go out verbatim: the server autolinks them, and an escape inside
  // one would end up in the link target.
  for (const match of value.matchAll(URL_TOKEN)) {
    writeProse(last, match.index);
    out += match[0];
    last = match.index + match[0].length;
  }
  writeProse(last, value.length);
  // A space before a line break comes out as `&#x20;` so it survives; the
  // server drops trailing spaces either way, and the entity would be what
  // the markdown mode and the notifications show.
  return out.replace(/(?<!\\)&#x(?:20|9);(?=\n|$)/g, ' ');
}

// The server renders `~~x~~` only: GFM's single-tilde form would strike
// through "~5 to ~10" in the composer and arrive as plain text.
const doubleTildeStrikethrough = $inputRule((ctx) => markRule(
  /(?<![\w:/])~~([^~]+?)~~(?![\w/])$/,
  strikethroughSchema.type(ctx),
));

function chatMarkdownStyle(ctx) {
  ctx.update(remarkStringifyOptionsCtx, (prev) => ({
    ...prev,
    bullet: '-',
    emphasis: '*',
    strong: '*',
    handlers: { ...prev.handlers, text: writeText },
  }));
}

// Milkdown closes a code span at its end, so text typed after it is plain.
// That also means Ctrl+E on an empty selection codes the next character
// only: the composer toggles code like bold, on until toggled off.
function stickyInlineCode(ctx) {
  ctx.update(inlineCodeSchema.key, (prev) => (innerCtx) => ({
    ...prev(innerCtx),
    inclusive: true,
  }));
}

function isDocEmpty(doc) {
  return doc.childCount === 1
    && doc.firstChild.isTextblock
    && doc.firstChild.content.size === 0;
}

// Marks the root while the document is empty so CSS can draw the placeholder.
const placeholderPlugin = $prose(() => new Plugin({
  props: {
    attributes: (state) => (isDocEmpty(state.doc) ? { class: 'is-empty' } : {}),
  },
}));

// Shift+Enter is the composer's "new line": a new line of code inside a code
// block, a new item inside a list (an empty item leaves the list), a line
// break anywhere else. Enter itself belongs to the page: it sends.
//
// A code block is left by Shift+Enter on its empty last line, the same
// gesture that leaves a list, so the writer is never trapped inside one.
function leaveCodeBlockOnEmptyLine(state, dispatch) {
  const { $head, empty } = state.selection;
  if (!empty || !$head.parent.type.spec.code) return false;
  if ($head.parentOffset !== $head.parent.content.size) return false;
  if (!$head.parent.textContent.endsWith('\n')) return false;
  if (dispatch) {
    const tr = state.tr.delete($head.pos - 1, $head.pos);
    const insertAt = tr.mapping.map($head.after());
    tr.insert(insertAt, state.schema.nodes.paragraph.create());
    tr.setSelection(TextSelection.create(tr.doc, insertAt + 1));
    dispatch(tr.scrollIntoView());
  }
  return true;
}

function insertLineBreak(state, dispatch) {
  if (dispatch) {
    dispatch(state.tr.replaceSelectionWith(state.schema.nodes.hardbreak.create()).scrollIntoView());
  }
  return true;
}

// splitListItem declines an empty item of a top-level list and leaves it to
// the next command to lift it out.
function liftEmptyListItem(state, dispatch) {
  const { $head, empty } = state.selection;
  if (!empty || $head.parent.content.size !== 0) return false;
  if ($head.node(-1)?.type !== state.schema.nodes.list_item) return false;
  return liftListItem(state.schema.nodes.list_item)(state, dispatch);
}

// Shift+Enter writes a line break, not a new block, so a list marker or a
// fence typed on the next line sits mid-paragraph, where the block input
// rules never look - yet the server, like the markdown mode, makes a block of
// it. The caret's line, and the offset where it starts within its paragraph,
// let both cases below first end the paragraph at that line break.
function caretLine(state) {
  const { $head } = state.selection;
  let lineStart = 0;
  $head.parent.forEach((child, offset) => {
    if (child.type.name === 'hardbreak' && offset < $head.parentOffset) lineStart = offset + 1;
  });
  return { lineStart, text: $head.parent.textBetween(lineStart, $head.parentOffset) };
}

function splitAtLineStart(state, lineStart) {
  const breakPos = state.selection.$head.start() + lineStart - 1;
  return state.tr.delete(breakPos, breakPos + 1).split(breakPos);
}

// Typing a fence then Shift+Enter opens a code block, as it would in the
// markdown mode - the input rule alone only fires on a trailing space.
function openCodeFence(state, dispatch) {
  const { $head, empty } = state.selection;
  if (!empty || $head.parent.type !== state.schema.nodes.paragraph) return false;
  if ($head.parentOffset !== $head.parent.content.size) return false;
  const { lineStart, text } = caretLine(state);
  const fence = /^```([\w+-]*)$/.exec(text);
  if (!fence) return false;
  if (dispatch) {
    const tr = lineStart > 0 ? splitAtLineStart(state, lineStart) : state.tr;
    const $line = tr.doc.resolve(tr.mapping.map($head.pos));
    const start = $line.start();
    tr.delete(start, $line.pos);
    tr.setBlockType(start, start, state.schema.nodes.code_block, { language: fence[1] });
    dispatch(tr.scrollIntoView());
  }
  return true;
}

const BLOCK_MARKER = /^(?:[-*+]|\d+[.)]|>|#{1,6}|```[\w+-]*)$/;

// The space after a block marker on a line of its own: end the paragraph
// there, then let the input rules see the marker at the start of a block.
function promoteLineOnBlockMarker(view, from, to, text) {
  if (text !== ' ') return false;
  const state = view.state;
  const { $head, empty } = state.selection;
  if (!empty || $head.parent.type !== state.schema.nodes.paragraph) return false;
  const { lineStart, text: line } = caretLine(state);
  if (lineStart === 0 || !BLOCK_MARKER.test(line)) return false;
  const tr = splitAtLineStart(state, lineStart);
  const mappedFrom = tr.mapping.map(from);
  const mappedTo = tr.mapping.map(to);
  view.dispatch(tr);
  const insert = () => view.state.tr.insertText(text, mappedFrom, mappedTo);
  const handled = view.someProp('handleTextInput', (f) => f(view, mappedFrom, mappedTo, text, insert));
  if (!handled) view.dispatch(insert());
  return true;
}

const newLineKeymap = $prose(() => keymap({
  'Shift-Enter': (state, dispatch, view) => chainCommands(
    openCodeFence,
    leaveCodeBlockOnEmptyLine,
    newlineInCode,
    splitListItem(state.schema.nodes.list_item),
    liftEmptyListItem,
    insertLineBreak,
  )(state, dispatch, view),
}));

// Reports every state update synchronously: document changes (Milkdown's
// listener plugin debounces, and the page reads the markdown back the moment
// Enter sends) and selection moves (the toolbar shows the marks at the caret).
function updateReporter(onUpdate) {
  return $prose(() => new Plugin({
    view: () => ({
      update(view, prevState) {
        onUpdate(!view.state.doc.eq(prevState.doc));
      },
    }),
  }));
}

/**
 * Mount a rich composer inside `root`.
 *
 * Options:
 * - markdown: initial content.
 * - placeholder: text shown while empty.
 * - onChange(markdown): called after every content change.
 * - onSelectionChange(): called after any update, the caret moving included.
 * - onKeydown(event): called before the editor handles a key; calling
 *   event.preventDefault() stops the editor from handling it.
 * - onPaste(event): same contract, for paste events (attachments).
 */
export async function createRichComposer(root, options = {}) {
  const {
    markdown = '',
    placeholder = '',
    onChange = () => {},
    onSelectionChange = () => {},
    onKeydown = () => {},
    onPaste = () => {},
  } = options;

  let lastMarkdown = markdown;
  let applyingExternal = false;
  let created = false;

  const editor = Editor.make()
    .config((ctx) => {
      ctx.set(rootCtx, root);
      ctx.set(defaultValueCtx, markdown);
      plainNewlineBreaks(ctx);
      chatMarkdownStyle(ctx);
      stickyInlineCode(ctx);
      ctx.update(editorViewOptionsCtx, (prev) => ({
        ...prev,
        attributes: {
          class: 'chat-rich-input',
          role: 'textbox',
          'aria-multiline': 'true',
          'aria-label': placeholder || 'Message',
          'data-placeholder': placeholder,
          spellcheck: 'true',
        },
        handleKeyDown: (_view, event) => {
          onKeydown(event);
          return event.defaultPrevented;
        },
        handleTextInput: promoteLineOnBlockMarker,
        handlePaste: (_view, event) => {
          onPaste(event);
          return event.defaultPrevented;
        },
        // Files dropped on the composer are attachments, handled by the
        // page's drop zone around the editor - never inline content.
        handleDrop: (_view, event) => Boolean(event.dataTransfer?.files?.length),
      }));
    })
    .use(commonmark)
    .use(gfm.filter((plugin) => plugin !== strikethroughInputRule))
    .use(doubleTildeStrikethrough)
    .use(history)
    .use(clipboard)
    .use(placeholderPlugin)
    .use(newLineKeymap)
    .use(updateReporter((docChanged) => {
      if (!created) return;
      if (docChanged && !applyingExternal) {
        lastMarkdown = serialize();
        onChange(lastMarkdown);
      }
      onSelectionChange();
    }));

  const view = () => editor.ctx.get(editorViewCtx);
  // remark ends every document with a newline the message never had, and a
  // space typed after a mark can reach the document as a non-breaking one.
  const serialize = () => editor.ctx.get(serializerCtx)(view().state.doc)
    .replace(/\u00a0/g, ' ')
    .replace(/\n+$/, '');

  await editor.create();

  // The editor can take over from the textarea mid-sentence: carry on from
  // where the writer was, at the end, rather than at the start.
  const initial = view();
  initial.dispatch(initial.state.tr.setSelection(TextSelection.atEnd(initial.state.doc)));
  created = true;

  return {
    get view() { return view(); },

    getMarkdown: () => lastMarkdown,

    // Replace the whole document - used when the page changes the message
    // text itself (conversation switch, draft restore, edit, send). A value
    // equal to the last one this editor produced is the echo of its own
    // change and is ignored, so typing never re-parses the document.
    setMarkdown(value) {
      const next = value || '';
      if (next === lastMarkdown) return;
      const doc = editor.ctx.get(parserCtx)(next);
      if (!doc) return;
      const state = view().state;
      const tr = state.tr.replaceWith(0, state.doc.content.size, doc.content);
      tr.setSelection(TextSelection.atEnd(tr.doc));
      tr.setMeta('addToHistory', false);
      applyingExternal = true;
      try {
        view().dispatch(tr);
      } finally {
        applyingExternal = false;
      }
      lastMarkdown = next;
    },

    focus: () => view().focus(),
    blur: () => view().dom.blur(),
    hasFocus: () => view().hasFocus(),
    isEmpty: () => isDocEmpty(view().state.doc),

    toggleMark(name) {
      const type = view().state.schema.marks[MARKS[name]];
      if (!type) return;
      toggleMark(type)(view().state, view().dispatch);
      view().focus();
    },

    isMarkActive(name) {
      const state = view().state;
      const type = state.schema.marks[MARKS[name]];
      if (!type) return false;
      const { from, to, empty, $from } = state.selection;
      if (empty) return Boolean(type.isInSet(state.storedMarks || $from.marks()));
      return state.doc.rangeHasMark(from, to, type);
    },

    selectedText() {
      const { from, to } = view().state.selection;
      return view().state.doc.textBetween(from, to, ' ');
    },

    // Link the selection to `href`, or insert `text` linked to it when
    // nothing is selected. An empty href removes the link.
    setLink(href, text) {
      const v2 = view();
      const state = v2.state;
      const type = state.schema.marks.link;
      const { from, to, empty } = state.selection;
      const tr = state.tr;
      if (empty) {
        if (!href) return;
        const label = text || href;
        tr.insert(from, state.schema.text(label, [type.create({ href })]));
        tr.setSelection(TextSelection.create(tr.doc, from + label.length));
        tr.removeStoredMark(type);
      } else {
        tr.removeMark(from, to, type);
        if (href) tr.addMark(from, to, type.create({ href }));
      }
      v2.dispatch(tr.scrollIntoView());
      v2.focus();
    },

    insertText(text) {
      const v2 = view();
      v2.dispatch(v2.state.tr.insertText(text).scrollIntoView());
      v2.focus();
    },

    // Text of the current block up to the caret - what the mention
    // autocomplete matches its `@query` against.
    textBeforeCursor() {
      const { $head } = view().state.selection;
      if (!$head.parent.isTextblock) return '';
      return $head.parent.textBetween(0, $head.parentOffset);
    },

    // Replace the `length` characters before the caret with `text`.
    replaceBeforeCursor(length, text) {
      const v2 = view();
      const { $head } = v2.state.selection;
      const from = Math.max($head.pos - length, $head.start());
      const tr = v2.state.tr.insertText(text, from, $head.pos);
      v2.dispatch(tr.scrollIntoView());
      v2.focus();
    },

    destroy: () => editor.destroy(),
  };
}
