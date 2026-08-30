// Entry point for the vendored Monaco editor, loaded by the text viewer with a
// dynamic import(). editor.main pulls in the editor core plus every language
// contribution; each language sits behind a dynamic import that --splitting
// turns into its own chunk, fetched the first time a file of that language is
// opened. Styles come from the monaco-theme.css entry and the workers from
// build:monaco-workers - the CSS imports here are dropped by --loader:.css=empty.
export * from 'monaco-editor/esm/vs/editor/editor.main.js';
