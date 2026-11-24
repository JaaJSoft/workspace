'use strict';

const { loadScripts } = require('./loader');

// The real getUserTimeFormat() and userClockOptions() of localtime.js, read
// on the given clock, for a script tested without the page that loads it.
function clockGlobals(timeFormat = null) {
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/common/static/ui/js/localtime.js',
  ], {
    document: {
      documentElement: { getAttribute: (name) => (name === 'data-time-format' ? timeFormat : null) },
      body: {},
      querySelectorAll: () => [],
    },
    MutationObserver: class { observe() {} },
  });
  return { getUserTimeFormat: ctx.getUserTimeFormat, userClockOptions: ctx.userClockOptions };
}

module.exports = { clockGlobals };
