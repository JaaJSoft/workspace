// Entry point for the vendored force-graph module behind the notes graph view.
// notes_graph.js imports it dynamically the first time the graph opens, from
// the URL the graph partial carries in data-force-graph-url.
export { default } from 'force-graph';
