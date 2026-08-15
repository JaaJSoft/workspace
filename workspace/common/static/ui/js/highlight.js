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
// Either way the text is HTML by the time the match runs while the query is
// still the raw string the user typed, so the query is HTML-escaped too: it is
// the escaped forms that have to line up. Matching a bare `&` or `"` against
// text holding `&amp;` and `&quot;` otherwise misses the hit, or marks half an
// entity and renders it as literal text.
//
// The one asymmetry is the apostrophe. escapeHtml emits `&#39;` for it, which
// is right for text this helper escaped itself, but the markdown renderer
// behind a pre-escaped body escapes only `&<>"` and leaves `'` alone - so
// escaping the query's would stop `don't` from ever matching there.
function highlightMatch(text, query, options) {
  const preEscaped = !!(options && options.escape === false);
  const source = preEscaped ? text : escapeHtml(text);
  if (!source || !query) return source;
  const term = preEscaped ? escapeHtml(query).replace(/&#39;/g, "'") : escapeHtml(query);
  // Escaped last: escapeHtml emits none of the regex metacharacters, so the
  // ones left to neutralize are the ones the user actually typed.
  const pattern = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return source.replace(
    new RegExp(`(${pattern})`, 'gi'),
    '<mark class="bg-warning/40 text-inherit rounded-sm px-0.5">$1</mark>'
  );
}
