// Shared HTML-escaping helper. Use it whenever a user-controlled string is
// about to land in an HTML sink (innerHTML, Alpine x-html, a template literal,
// or a library that renders its input as HTML such as force-graph's nodeLabel).
// It turns the markup metacharacters into entities so the value renders as
// literal text and can neither inject elements nor break out of an attribute.
//
// Implemented as a pure string transform rather than the
// `el.textContent = s; el.innerHTML` DOM round-trip so it runs (and is
// unit-testable) outside a browser - e.g. in the node:vm test loader, which has
// no DOM. Escaping both quote characters keeps it safe in attribute contexts
// (alt="...", title='...'), not just element-text contexts. Single quotes use
// the numeric entity &#39; rather than &apos;, which is not valid in HTML 4.
//
// `&` MUST be replaced first: otherwise the `&` introduced by a later
// replacement (e.g. `<` -> `&lt;`) would itself be re-escaped into `&amp;lt;`.
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
