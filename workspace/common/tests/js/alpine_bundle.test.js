// Vérifie l'intégrité de l'artefact Alpine vendorisé. Ce test ne peut pas
// exécuter Alpine (il exige un vrai DOM, et le runner de tests JS du projet
// interdit toute dépendance npm) : la vérification comportementale est faite
// par les suites Playwright. Ici on verrouille ce qui casserait silencieusement
// le chargement — mauvais format de module, global manquant, version flottante.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(
  REPO_ROOT, 'workspace', 'common', 'static', 'ui', 'js', 'vendor', 'alpine', 'alpine.js'
);
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'alpine', 'package.json');

test('le bundle existe et n\'est pas vide', () => {
  assert.ok(fs.existsSync(BUNDLE), `artefact absent : ${BUNDLE}`);
  assert.ok(fs.statSync(BUNDLE).size > 10_000, 'artefact suspicieusement petit');
});

test('le bundle est au format IIFE, pas ESM', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // Une sortie ESM porterait des déclarations import/export en tête de fichier.
  // Chargée via <script defer> sans type="module", elle lèverait une
  // SyntaxError et Alpine ne démarrerait jamais.
  assert.doesNotMatch(src, /^\s*import\s/m, 'déclaration import trouvée : sortie ESM');
  assert.doesNotMatch(src, /^\s*export\s/m, 'déclaration export trouvée : sortie ESM');
});

test('le bundle expose window.Alpine', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // stores.js, avatar.js et chat/sse.js lisent tous le global Alpine.
  assert.match(src, /window\.Alpine\s*=/, 'window.Alpine n\'est jamais assigné');
});

test('les versions sont épinglées exactement', () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const deps = { ...manifest.dependencies, ...manifest.devDependencies };
  assert.ok(Object.keys(deps).length > 0, 'aucune dépendance déclarée');
  for (const [name, range] of Object.entries(deps)) {
    assert.match(
      range, /^\d+\.\d+\.\d+$/,
      `${name} vaut "${range}" : une version flottante rend le bundle non reproductible`
    );
  }
});

test('le verrou de dépendances est committé', () => {
  assert.ok(
    fs.existsSync(path.join(REPO_ROOT, 'scripts', 'alpine', 'package-lock.json')),
    'package-lock.json manquant : les reconstructions ne seraient pas reproductibles'
  );
});
