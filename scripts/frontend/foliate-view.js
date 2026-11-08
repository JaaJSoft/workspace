// Entry point for the vendored foliate-js e-book renderer behind the files
// e-book reader. ebook_reader.js imports it dynamically from the URL the
// reader page carries in data-foliate-url; defining <foliate-view> is the
// import's side effect. Each format's parser stays a lazy chunk.
export { makeBook } from 'foliate-js/view.js';
