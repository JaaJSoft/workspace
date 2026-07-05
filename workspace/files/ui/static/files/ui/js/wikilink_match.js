// Pure, dependency-free helpers for the "[[" wikilink trigger.
// Kept separate from wikilink_autocomplete.js (which needs the Milkdown
// bundle) so unit tests and the e2e helper check can import it with no
// editor dependency.

// Matches an open "[[" followed by a query containing no "]" / "[" / newline,
// anchored to the END of the text before the caret. Returns { query, length }
// where length spans "[[" + query, or null when the trigger is not active.
export function matchTrigger(textBeforeCaret) {
  const m = /\[\[([^\[\]\n]*)$/.exec(textBeforeCaret || '');
  if (!m) return null;
  return { query: m[1], length: m[0].length };
}

// Given the caret's absolute document position and the matched length from
// matchTrigger, return the [from, to) range that spans "[[query" so it can be
// replaced by the chosen note link.
export function replacementRange(caretPos, matchLength) {
  return { from: caretPos - matchLength, to: caretPos };
}
