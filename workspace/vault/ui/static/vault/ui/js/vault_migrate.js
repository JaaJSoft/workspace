// Moving what this account wrote under a superseded algorithm to the current
// suite, after an unlock, without asking anything of the user.
//
// The server lists the rows; nothing listed is trusted. Every row is read and
// verified the way the listing reads it, and only a verified row is rebuilt -
// from the verified object, never from the raw answer. A vault whose own
// metadata does not verify is left alone entirely, its key wrap included: the
// name opening under the key is what proves the key is the vault's.
//
// There is no progress to keep: the server's listing is the state, so a pass
// cut short by a lock or a closed tab is simply finished by the next unlock.
window.vaultMigration = (function () {
  const MAX_ITEMS = 200;
  const MAX_CIPHERTEXTS = 2000;
  const MAX_BYTES = 1000000;
  const WARN_AFTER = 3;
  const FAILURES_KEY = 'vault.migration.failures';

  function lockedError() {
    const error = new Error('locked');
    error.reason = 'locked';
    return error;
  }

  function readFailures() {
    try {
      return Number(window.localStorage.getItem(FAILURES_KEY)) || 0;
    } catch (err) {
      return null;
    }
  }

  function writeFailures(value) {
    try {
      window.localStorage.setItem(FAILURES_KEY, String(value));
    } catch (err) {
      /* the warning simply never shows on this device */
    }
  }

  function ciphertexts(item) {
    let count = item.fields ? Object.keys(item.fields).length : 0;
    ['encrypted_name', 'encrypted_description', 'encrypted_notes'].forEach(function (key) {
      if (item[key]) count += 1;
    });
    return count;
  }

  function batches(items) {
    const out = [];
    let current = [];
    let count = 0;
    let bytes = 0;
    for (const item of items) {
      const size = JSON.stringify(item).length;
      const more = ciphertexts(item);
      if (current.length && (current.length === MAX_ITEMS || count + more > MAX_CIPHERTEXTS || bytes + size > MAX_BYTES)) {
        out.push(current);
        current = [];
        count = 0;
        bytes = 0;
      }
      current.push(item);
      count += more;
      bytes += size;
    }
    if (current.length) out.push(current);
    return out;
  }

  function resignable(row) {
    return window.vaultCrypto.mayResign(window.vaultCrypto.fromBase64Url(row.metadata_sig)[0]);
  }

  // The builders sign the vault's UUID into every rewrite, so a row listed
  // under this vault but carrying another one must never reach them.
  function pick(rows, uuids, vaultRow) {
    const wanted = new Set(uuids.map(String));
    return rows.filter(function (row) {
      return wanted.has(String(row.uuid)) && String(row.vault) === String(vaultRow.uuid);
    });
  }

  async function buildItems(session, listed, vaultRow) {
    const api = window.vaultApi;
    const reader = window.vaultReader;
    const opened = await reader.readVault(session, vaultRow);
    if (opened.tampered || opened.unsupported || opened.unreadable || opened.unopenable) return [];
    const items = [];
    if (listed.wrap) {
      const rewrapped = await session.rewrapVaultKey(opened);
      items.push({
        kind: 'wrap',
        wrapped_key: rewrapped.wrapped_key,
        hpke_suite: rewrapped.hpke_suite,
        wrapped_key_expected: vaultRow.wrapped_key,
      });
    }
    if (listed.metadata && resignable(vaultRow)) {
      items.push(await window.buildVaultMetadataMigrateItem(session, opened));
    }
    if (listed.folders.length) {
      const read = await reader.readFolders(session, opened, pick(await api.listFolders(vaultRow.uuid), listed.folders, vaultRow));
      for (const row of read.verifiedRows.filter(resignable)) {
        items.push(await window.buildFolderMigrateItem(session, opened, row));
      }
    }
    if (listed.tags.length) {
      const read = await reader.readTags(session, opened, pick(await api.listTags(vaultRow.uuid), listed.tags, vaultRow));
      for (const row of read.verifiedRows.filter(resignable)) {
        items.push(await window.buildTagMigrateItem(session, opened, row));
      }
    }
    if (listed.entries.length) {
      const [live, trashed] = await Promise.all([
        api.listEntries(vaultRow.uuid),
        api.listEntries(vaultRow.uuid, { trashed: true }),
      ]);
      // The reader verifies each entry, then the builder reseals it: both ask
      // for the same entry key, which the cache derives once.
      await session.withEntryKeyCache(async function () {
        const read = await reader.readEntries(session, opened, pick(live.concat(trashed), listed.entries, vaultRow));
        for (const row of read.verifiedRows.filter(resignable)) {
          items.push(await window.buildEntryMigrateItem(session, opened, row));
        }
      });
    }
    return items;
  }

  // 'done' | 'abandoned' (counts) | 'stop' (counts) | 'locked'.
  async function migrateVault(session, listed, retried) {
    const vaults = await window.vaultApi.listVaults();
    const vaultRow = vaults.find(function (row) { return String(row.uuid) === String(listed.uuid); });
    if (!vaultRow) return { status: 'done', wrote: false };
    const items = await buildItems(session, listed, vaultRow);
    let wrote = false;
    for (const batch of batches(items)) {
      if (!session.isUnlocked()) throw lockedError();
      try {
        await window.vaultApi.migrateVault(vaultRow.uuid, batch);
        wrote = true;
      } catch (err) {
        if (err && err.reason === 'locked') throw err;
        const status = err && err.status;
        if (status === 409) {
          if (retried) return { status: 'done', wrote: wrote };
          const fresh = await window.vaultApi.fetchMigration();
          const again = fresh.vaults.find(function (item) { return String(item.uuid) === String(listed.uuid); });
          if (!again) return { status: 'done', wrote: wrote };
          const next = await migrateVault(session, again, true);
          return { status: next.status, wrote: wrote || next.wrote };
        }
        if (status === 400 || status === 404) return { status: 'abandoned', wrote: wrote };
        return { status: 'stop', wrote: wrote };
      }
    }
    return { status: 'done', wrote: wrote };
  }

  async function pass(session) {
    let failed = false;
    let wrote = false;
    try {
      const listing = await window.vaultApi.fetchMigration();
      for (const listed of listing.vaults) {
        const result = await migrateVault(session, listed, false);
        wrote = wrote || result.wrote;
        if (result.status === 'abandoned') failed = true;
        if (result.status === 'stop') { failed = true; break; }
      }
    } catch (err) {
      if (err && err.reason === 'locked') return { outcome: 'locked', wrote: wrote, warn: false };
      failed = true;
    }
    const previous = readFailures();
    if (previous === null) return { outcome: failed ? 'failed' : 'clean', wrote: wrote, warn: false };
    const next = failed ? previous + 1 : 0;
    writeFailures(next);
    return { outcome: failed ? 'failed' : 'clean', wrote: wrote, warn: next >= WARN_AFTER };
  }

  let inFlight = false;

  // One pass at a time: a second caller while one runs gets 'busy' and does
  // nothing, so two passes never rewrite the same rows against each other.
  async function run(session) {
    if (inFlight) return { outcome: 'busy', wrote: false, warn: false };
    inFlight = true;
    try {
      return await pass(session);
    } finally {
      inFlight = false;
    }
  }

  return {
    MAX_ITEMS: MAX_ITEMS,
    MAX_CIPHERTEXTS: MAX_CIPHERTEXTS,
    MAX_BYTES: MAX_BYTES,
    WARN_AFTER: WARN_AFTER,
    run: run,
  };
})();
