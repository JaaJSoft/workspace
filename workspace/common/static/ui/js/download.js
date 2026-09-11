// Handing the user a file the page built. Two things here are load-bearing and
// were both learned from a screen where getting it wrong lost data:
//
// 1. The anchor is inserted into the document before it is clicked. Firefox
//    ignores a click on a detached anchor, and the user gets no file and no
//    error.
// 2. The object URL is revoked, but only after the browser has had the click.
//    Revoking synchronously races the read the click just started.
window.downloadBlob = function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(function() {
    URL.revokeObjectURL(url);
  }, 0);
};
