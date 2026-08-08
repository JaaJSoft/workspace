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
  return loadScripts([repoRelativePath], extraGlobals);
}

/**
 * Same, for a script that depends on a global another script defines
 * (base.html loads both, so they share one global scope). Scripts run in
 * order in a single context; the last one sees everything before it.
 *
 * @param {string[]} repoRelativePaths - scripts to run, in load order
 * @param {object} extraGlobals - additional globals exposed to the scripts
 * @returns {object} the contextified sandbox holding their globals
 */
function loadScripts(repoRelativePaths, extraGlobals = {}) {
  const sandbox = { console, ...extraGlobals };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const repoRelativePath of repoRelativePaths) {
    const code = fs.readFileSync(path.join(REPO_ROOT, repoRelativePath), 'utf8');
    vm.runInContext(code, sandbox, { filename: repoRelativePath });
  }
  return sandbox;
}

/**
 * Minimal browser globals for scripts that define a custom element at
 * load time. Enough for the definition to run; the element's behaviour
 * belongs in an e2e test with a real DOM.
 */
const CUSTOM_ELEMENT_STUBS = {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: () => {} },
  document: {
    createElement: () => ({ setAttribute() {}, addEventListener() {} }),
  },
};

module.exports = { loadScript, loadScripts, CUSTOM_ELEMENT_STUBS };
