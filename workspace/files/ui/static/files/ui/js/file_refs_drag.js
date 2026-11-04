// Files dragged as references: a listing row of Files or Notes carries the
// files it stands for, and the markdown editor embeds them where they are
// dropped (file_embed.js). The payload says which files, never their bytes.
//
// The source declares an effectAllowed holding 'copy', the editor asks for
// 'copy' on dragover: the browser refuses a drop whose effects do not meet.
window.fileRefsDrag = (function () {
  const MIME = 'application/x-workspace-file-refs';

  // refs: [{uuid, name}]
  function write(dataTransfer, refs) {
    const clean = (refs || [])
      .filter((ref) => ref && ref.uuid)
      .map((ref) => ({ uuid: String(ref.uuid), name: String(ref.name || '') }));
    if (!clean.length) return false;
    dataTransfer.setData(MIME, JSON.stringify(clean));
    return true;
  }

  // Whether a drag in progress carries references; dragover can only see
  // the types, the data itself is readable at drop time.
  function has(dataTransfer) {
    return !!dataTransfer && Array.from(dataTransfer.types || []).includes(MIME);
  }

  // The references of a drop, [] when it carries none or garbage.
  function read(dataTransfer) {
    if (!has(dataTransfer)) return [];
    let refs;
    try {
      refs = JSON.parse(dataTransfer.getData(MIME));
    } catch (e) {
      return [];
    }
    if (!Array.isArray(refs)) return [];
    return refs
      .filter((ref) => ref && typeof ref.uuid === 'string' && window.isValidUuid(ref.uuid))
      .map((ref) => ({ uuid: ref.uuid.toLowerCase(), name: typeof ref.name === 'string' ? ref.name : '' }));
  }

  return { MIME, write, has, read };
})();
