'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

function make({ pickerResult, fetchImpl } = {}) {
  const fetchCalls = [];
  const dialogCalls = [];
  const ctx = loadScript('workspace/common/static/ui/js/attachment_input.js', {
    AppDialog: {
      filePicker: async (opts) => {
        dialogCalls.push({ kind: 'file', opts });
        return pickerResult;
      },
      folderPicker: async (opts) => {
        dialogCalls.push({ kind: 'folder', opts });
        return pickerResult;
      },
      message: (opts) => dialogCalls.push({ kind: 'message', opts }),
      error: (opts) => dialogCalls.push({ kind: 'error', opts }),
    },
    getCSRFToken: () => 'tok',
    fetch:
      fetchImpl ||
      (async (url, opts) => {
        fetchCalls.push({ url, opts });
        return { ok: true, json: async () => ({}) };
      }),
    URL: {
      createObjectURL: () => 'blob:preview',
      revokeObjectURL: () => {},
    },
  });
  const comp = ctx.attachmentInputMixin({ pickerMessage: 'Pick.' });
  comp.$refs = {};
  return { comp, fetchCalls, dialogCalls };
}

test('addFiles stages files, previews media, dedupes by name+size', () => {
  const { comp } = make();
  const img = { name: 'a.png', size: 3, type: 'image/png' };
  const doc = { name: 'b.txt', size: 5, type: 'text/plain' };
  comp.addFiles([img, doc]);
  comp.addFiles([{ name: 'a.png', size: 3, type: 'image/png' }]);
  assert.equal(comp.pendingFiles.length, 2);
  assert.equal(comp.pendingFiles[0]._preview, 'blob:preview');
  assert.equal(comp.pendingFiles[1]._preview, undefined);
  assert.equal(comp.hasPendingAttachments(), true);
});

test('addFiles fires the attachmentsAdded hook only when something landed', () => {
  const { comp } = make();
  let fired = 0;
  comp.attachmentsAdded = () => fired++;
  comp.addFiles([{ name: 'a.txt', size: 1, type: 'text/plain' }]);
  comp.addFiles([{ name: 'a.txt', size: 1, type: 'text/plain' }]);
  assert.equal(fired, 1);
});

test('attachFromWorkspace stages picked files and dedupes by uuid', async () => {
  const { comp } = make({
    pickerResult: [
      { uuid: 'u1', name: 'one.txt' },
      { uuid: 'u2', name: 'two.txt' },
    ],
  });
  await comp.attachFromWorkspace();
  await comp.attachFromWorkspace();
  assert.equal(comp.pendingPickedFiles.length, 2);
});

test('attachFromWorkspace is inert when the picker is cancelled', async () => {
  const { comp } = make({ pickerResult: null });
  let fired = 0;
  comp.attachmentsAdded = () => fired++;
  await comp.attachFromWorkspace();
  assert.equal(comp.pendingPickedFiles.length, 0);
  assert.equal(fired, 0);
});

test('remove/clear empty the staged lists', () => {
  const { comp } = make();
  comp.addFiles([{ name: 'a.txt', size: 1, type: 'text/plain' }]);
  comp.pendingPickedFiles.push({ uuid: 'u1', name: 'one.txt' });
  comp.removeFile(0);
  assert.equal(comp.pendingFiles.length, 0);
  comp.removePickedFile(0);
  assert.equal(comp.pendingPickedFiles.length, 0);
  comp.addFiles([{ name: 'a.txt', size: 1, type: 'text/plain' }]);
  comp.pendingPickedFiles.push({ uuid: 'u1', name: 'one.txt' });
  comp.clearAttachments();
  assert.equal(comp.hasPendingAttachments(), false);
});

test('appendAttachmentsTo appends both sources under the given keys', () => {
  const { comp } = make();
  const doc = { name: 'a.txt', size: 1, type: 'text/plain' };
  comp.addFiles([doc]);
  comp.pendingPickedFiles.push({ uuid: 'u1', name: 'one.txt' });
  const appended = [];
  const formData = { append: (k, v) => appended.push([k, v]) };
  comp.appendAttachmentsTo(formData, 'attachments', 'file_uuids');
  assert.deepEqual(appended, [
    ['attachments', doc],
    ['file_uuids', 'u1'],
  ]);
});

test('drop and paste route files through addFiles', () => {
  const { comp } = make();
  comp.handleDrop({
    dataTransfer: { files: [{ name: 'd.txt', size: 2, type: 'text/plain' }] },
  });
  let prevented = false;
  comp.handlePaste({
    preventDefault: () => {
      prevented = true;
    },
    clipboardData: {
      items: [
        {
          kind: 'file',
          getAsFile: () => ({ name: 'p.txt', size: 4, type: 'text/plain' }),
        },
        { kind: 'string', getAsFile: () => null },
      ],
    },
  });
  assert.equal(comp.pendingFiles.length, 2);
  assert.equal(prevented, true);
  assert.equal(comp.isDraggingOver, false);
});

test('promptSaveAttachmentToFiles posts the chosen folder', async () => {
  const { comp, fetchCalls, dialogCalls } = make({
    pickerResult: { uuid: 'folder-1', name: 'Docs' },
  });
  await comp.promptSaveAttachmentToFiles('/api/v1/chat/attachments/a1/save-to-files');
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, '/api/v1/chat/attachments/a1/save-to-files');
  assert.equal(fetchCalls[0].opts.body, '{"folder_id":"folder-1"}');
  assert.ok(dialogCalls.some((c) => c.kind === 'message'));
});

test('promptSaveAttachmentToFiles is inert when the picker is cancelled', async () => {
  const { comp, fetchCalls } = make({ pickerResult: null });
  await comp.promptSaveAttachmentToFiles('/x');
  assert.equal(fetchCalls.length, 0);
});
