// Entry point for the vendored emoji picker. Importing the package registers
// the <emoji-picker> custom element. The emoji list is not in the bundle: the
// element fetches it at runtime from its `data-source` attribute, and the
// library's default for that attribute is a CDN URL - every <emoji-picker> in
// a template must point it at the vendored data.json (build:emoji-data).
import 'emoji-picker-element';
