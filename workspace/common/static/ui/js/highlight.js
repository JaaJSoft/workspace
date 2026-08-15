// Wraps every occurrence of `query` inside `text` in a <mark>, for the search
// panels that render their results through x-html. Shared so that a hit looks
// the same whichever panel found it, and so the regex-escaping of the
// user-typed term is written once.
//
// The text is HTML-escaped first: callers feed it user-controlled strings (file
// names, mail subjects, contact names, message bodies). Pass `{ escape: false }`
// only when the input is already server-rendered, sanitized HTML - chat message
// bodies are the sole such caller.
//
// The query is escaped as a *regex*, not as HTML, because it never reaches the
// output directly: only the `$1` backreference does, and that is a slice of the
// already-escaped text. The mismatch has a known consequence - a query holding
// an HTML metacharacter (`&`, `<`, `"`) is compared against its escaped
// counterpart in the text, so it either fails to match or splits an entity.
// Behaviour carried over from the four copies this helper replaces.
function highlightMatch(text, query, options) {
  const source = options && options.escape === false ? text : escapeHtml(text);
  if (!source || !query) return source;
  const term = String(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return source.replace(
    new RegExp(`(${term})`, 'gi'),
    '<mark class="bg-warning/40 text-inherit rounded-sm px-0.5">$1</mark>'
  );
}
