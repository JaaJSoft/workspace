// Pure helper for inserting / swapping a plain-text email signature inside the
// compose textarea body. No DOM access - unit-tested via node --test.
window.mailSignature = (function () {
  // Quote marker produced by replyTo / replyAll / forwardMessage in mail_compose.js.
  var QUOTE_MARKER = '\n\n---\n';

  function buildBlock(signature) {
    var sig = (signature || '').trim();
    return sig ? '\n-- \n' + sig + '\n' : '';
  }

  function applySignature(body, signature) {
    var src = body || '';
    var block = buildBlock(signature);
    if (!block) return { body: src, block: '' };
    var idx = src.indexOf(QUOTE_MARKER);
    if (idx === -1) return { body: src + block, block: block };
    return { body: src.slice(0, idx) + block + src.slice(idx), block: block };
  }

  function swapSignature(body, oldBlock, newSignature) {
    var src = body || '';
    var newBlock = buildBlock(newSignature);
    if (oldBlock && src.indexOf(oldBlock) !== -1) {
      return { body: src.replace(oldBlock, newBlock), block: newBlock };
    }
    return applySignature(src, newSignature);
  }

  return {
    buildBlock: buildBlock,
    applySignature: applySignature,
    swapSignature: swapSignature,
  };
})();
