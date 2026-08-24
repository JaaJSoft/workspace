/**
 * Copies the published Lucide UMD build into the static tree.
 *
 * A copy, not a bundle: identical bytes are what let lucide_bundle.test.js
 * check the artifact against the digest the published build carries.
 *
 * The one edit is the trailing sourceMappingURL comment. collectstatic's
 * manifest storage resolves that reference at deploy time and fails the whole
 * command over a map we do not ship.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const SOURCE = 'node_modules/lucide/dist/umd/lucide.min.js';
const TARGET_DIR = '../../workspace/common/static/ui/js/vendor/lucide';
const SOURCE_MAP_COMMENT = /\r?\n\/\/# sourceMappingURL=\S*\r?\n?$/;

const published = readFileSync(SOURCE, 'utf8');
if (!SOURCE_MAP_COMMENT.test(published)) {
  throw new Error(`${SOURCE} no longer ends with a sourceMappingURL comment`);
}

mkdirSync(TARGET_DIR, { recursive: true });
writeFileSync(`${TARGET_DIR}/lucide.js`, published.replace(SOURCE_MAP_COMMENT, '\n'));
console.log(`vendored ${SOURCE} -> ${TARGET_DIR}/lucide.js`);
