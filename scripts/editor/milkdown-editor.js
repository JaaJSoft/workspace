// Single-entry vendored bundle for the markdown editor.
// Crepe and the slash-plugin symbols MUST come from one module graph so they
// share Milkdown/ProseMirror internals (instanceof checks break otherwise).
export { Crepe } from '@milkdown/crepe';
export { slashFactory, SlashProvider } from '@milkdown/plugin-slash';
