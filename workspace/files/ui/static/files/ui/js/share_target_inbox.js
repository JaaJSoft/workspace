/**
 * Files another app shared with the installed app (the manifest's
 * share_target), kept between the service worker that receives them and the
 * Files page that asks where to save them.
 *
 * The share arrives as a multipart POST that carries no CSRF token, so the
 * service worker answers it without the server: it stores the files under a
 * fresh id and redirects to /files?share_target=<id>. IndexedDB stores File
 * objects as they are, so the page gets back the names and types the other
 * app sent.
 *
 * Loaded by sw.js (importScripts) and by the Files page: `self` is the
 * global in both.
 *
 *   const id = await shareTargetInbox.put(files);
 *   const files = await shareTargetInbox.get(id);   // null once removed
 *   await shareTargetInbox.remove(id);
 */
self.shareTargetInbox = (function () {
  const DB_NAME = 'workspace-share-target';
  const STORE = 'shares';
  // A share nobody claimed (the app was closed on the login page) is dropped
  // the next time one arrives, rather than holding its bytes forever.
  const MAX_AGE_MS = 24 * 60 * 60 * 1000;

  function openDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(STORE, { keyPath: 'id' });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  // Runs `work` in one transaction and resolves with what it returned once
  // the transaction commits: a write that resolved earlier could still be
  // rolled back.
  async function transact(mode, work) {
    const db = await openDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        let outcome;
        tx.oncomplete = () => resolve(outcome);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
        outcome = work(tx.objectStore(STORE));
      });
    } finally {
      db.close();
    }
  }

  return {
    put(files) {
      const id = crypto.randomUUID();
      const now = Date.now();
      return transact('readwrite', (store) => {
        store.openCursor().onsuccess = (event) => {
          const cursor = event.target.result;
          if (!cursor) return;
          if (now - cursor.value.created > MAX_AGE_MS) cursor.delete();
          cursor.continue();
        };
        store.put({ id, created: now, files: Array.from(files) });
        return id;
      });
    },

    get(id) {
      return transact('readonly', (store) => {
        const result = { files: null };
        store.get(id).onsuccess = (event) => {
          result.files = event.target.result ? event.target.result.files : null;
        };
        return result;
      }).then((result) => result.files);
    },

    remove(id) {
      return transact('readwrite', (store) => {
        store.delete(id);
      });
    },
  };
})();
