'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// workspace/common/tests/js -> repo root
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');

/**
 * Execute a production frontend script in an isolated vm context and return
 * that context so tests can call the globals the script defined.
 *
 * Production JS files are classic scripts (top-level function declarations
 * and `window.X = ...` assignments), not ES modules, so they cannot be
 * require()d or import()ed directly. Running them in a vm context mirrors
 * how the browser loads them via <script src>.
 *
 * Notes:
 * - `window` is the context object itself, mirroring the browser where
 *   `window === globalThis`. Both `function f() {}` declarations and
 *   `window.f = ...` assignments end up readable on the returned context.
 * - Top-level `const`/`let` bindings live in the script's global lexical
 *   scope and are NOT reachable on the returned context - only `function`
 *   and `var` declarations are. Test the public surface, not internals.
 * - Scripts that touch browser APIs at load time (document, fetch, ...)
 *   get them via `extraGlobals`: loadScript(path, { document: stub }).
 *
 * @param {string} repoRelativePath - script path from the repo root, e.g.
 *   'workspace/common/static/ui/js/uuid.js'
 * @param {object} extraGlobals - additional globals exposed to the script
 * @returns {object} the contextified sandbox holding the script's globals
 */
function loadScript(repoRelativePath, extraGlobals = {}) {
  const code = fs.readFileSync(path.join(REPO_ROOT, repoRelativePath), 'utf8');
  const sandbox = { console, ...extraGlobals };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: repoRelativePath });
  return sandbox;
}

module.exports = { loadScript };
