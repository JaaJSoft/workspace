/**
 * Copies published builds into the static tree, byte for byte.
 *
 * A copy, not a bundle: identical bytes are what let a *_bundle.test.js check
 * an artifact against the digest the published build carries. Libraries that
 * ship a self-contained global build (FullCalendar, luxon, Cropper, Lucide)
 * and plain data files go through here; everything else is an esbuild entry.
 *
 * The one edit is a trailing sourceMappingURL comment. collectstatic's
 * manifest storage resolves that reference at deploy time and fails the whole
 * command over a map we do not ship.
 *
 * Usage: node vendor-copy.mjs <set> [<set>...]
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const WORKSPACE = '../../workspace';

const SETS = {
  lucide: [
    [
      'node_modules/lucide/dist/umd/lucide.min.js',
      `${WORKSPACE}/common/static/ui/js/vendor/lucide/lucide.js`,
    ],
  ],
  calendar: [
    [
      'node_modules/fullcalendar/index.global.min.js',
      `${WORKSPACE}/calendar/ui/static/calendar/ui/js/vendor/fullcalendar/fullcalendar.js`,
    ],
    [
      'node_modules/luxon/build/global/luxon.min.js',
      `${WORKSPACE}/calendar/ui/static/calendar/ui/js/vendor/fullcalendar/luxon.js`,
    ],
    [
      'node_modules/@fullcalendar/luxon3/index.global.min.js',
      `${WORKSPACE}/calendar/ui/static/calendar/ui/js/vendor/fullcalendar/luxon3.js`,
    ],
  ],
  cropper: [
    [
      'node_modules/cropperjs/dist/cropper.min.js',
      `${WORKSPACE}/users/ui/static/users/ui/js/vendor/cropper/cropper.js`,
    ],
    [
      'node_modules/cropperjs/dist/cropper.min.css',
      `${WORKSPACE}/users/ui/static/users/ui/css/vendor/cropper/cropper.css`,
    ],
  ],
  'emoji-data': [
    [
      'node_modules/emoji-picker-element-data/en/emojibase/data.json',
      `${WORKSPACE}/chat/ui/static/chat/ui/js/vendor/emoji-picker/data.json`,
    ],
  ],
};

// `//# sourceMappingURL=x.map` in JS, `/*# sourceMappingURL=x.map */` in CSS.
const SOURCE_MAP_COMMENT = /\r?\n(?:\/\/# sourceMappingURL=\S*|\/\*# sourceMappingURL=\S* \*\/)\r?\n?$/;

const requested = process.argv.slice(2);
if (requested.length === 0) {
  throw new Error(`usage: node vendor-copy.mjs <set>; sets: ${Object.keys(SETS).join(', ')}`);
}

for (const name of requested) {
  const files = SETS[name];
  if (!files) throw new Error(`unknown set "${name}"; sets: ${Object.keys(SETS).join(', ')}`);
  for (const [source, target] of files) {
    const published = readFileSync(source, 'utf8');
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, published.replace(SOURCE_MAP_COMMENT, '\n'));
    console.log(`vendored ${source} -> ${target}`);
  }
}
