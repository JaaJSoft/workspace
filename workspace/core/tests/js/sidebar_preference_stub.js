'use strict';

// The window.sidebarPreference a module component's test hands its scripts:
// the real one, run in a context of its own where the shell's rendered value,
// the settings write and the viewport width are the test's to choose.
const { loadScripts } = require('../../../common/tests/js/loader');

function sidebarPreferenceStub({ initial = false, save = () => {}, narrow = false } = {}) {
  const ctx = loadScripts(['workspace/core/static/core/js/sidebar_preference.js'], {
    document: { getElementById: () => ({ textContent: JSON.stringify(initial) }) },
    saveUserSetting: (module, key, value) => {
      save(module, value);
      return Promise.resolve();
    },
    matchMedia: () => ({ matches: narrow }),
  });
  return ctx.sidebarPreference;
}

module.exports = { sidebarPreferenceStub };
