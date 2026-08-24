/**
 * Copies the published Lucide UMD build into the static tree.
 *
 * A copy rather than a bundle: base.html loaded this exact file from unpkg
 * under an SRI hash, and keeping the bytes is what makes the swap provable
 * (workspace/common/tests/js/lucide_bundle.test.js re-checks the digest).
 *
 * The one edit is the trailing sourceMappingURL comment. The published map is
 * 3.9 MB and we do not ship it, and collectstatic's manifest storage resolves
 * that reference at deploy time - it fails the whole command when the file is
 * missing, so leaving the comment in breaks production builds.
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
