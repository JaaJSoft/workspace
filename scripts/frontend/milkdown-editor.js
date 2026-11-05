// Single-entry vendored bundle for the markdown editor.
// Crepe and the plugin-building symbols MUST come from one module graph so
// they share Milkdown/ProseMirror internals (instanceof checks break
// otherwise): the slash plugin behind the "[[" autocomplete, and the
// composables and ProseMirror Plugin class behind the file embed node
// (files/ui/js/file_embed.js).
export { Crepe } from '@milkdown/crepe';
export { slashFactory, SlashProvider } from '@milkdown/plugin-slash';
export { $nodeSchema, $prose, $remark, $view } from '@milkdown/kit/utils';
export { Plugin } from '@milkdown/kit/prose/state';
