'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const MEMORIES = [
  { id: 1, key: 'prenom', content: "Le prénom de l'utilisateur est Demo" },
  { id: 2, key: 'boisson', content: 'Aime le café sans sucre' },
  { id: 3, key: 'fuseau', content: 'Vit à Paris' },
];

function loadBotMixin() {
  return loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', {
    document: { getElementById: () => null },
  });
}

/**
 * chatApp() composes its mixins with object spread, so anything the mixin
 * exposes has to survive `{...chatBotMixin()}`. A getter does not: spread
 * copies its value at spread time, freezing the memory list to the empty
 * array it held before the fetch. Build the component the same way the
 * real page does, so the tests exercise that path.
 */
function buildApp() {
  const app = { ...loadBotMixin().chatBotMixin() };
  app.botMemories = MEMORIES;
  return app;
}

test('the memory filter survives the mixin spread and returns every memory', () => {
  const app = buildApp();

  assert.deepStrictEqual(
    Array.from(app.filteredBotMemories()).map(m => m.key),
    ['prenom', 'boisson', 'fuseau'],
  );
});

test('the memory filter matches on key and content, case-insensitively', () => {
  const app = buildApp();

  app.memorySearch = 'PRENOM';
  assert.deepStrictEqual(Array.from(app.filteredBotMemories()).map(m => m.id), [1]);

  app.memorySearch = 'café';
  assert.deepStrictEqual(Array.from(app.filteredBotMemories()).map(m => m.id), [2]);

  app.memorySearch = 'nothing here';
  assert.deepStrictEqual(Array.from(app.filteredBotMemories()), []);
});

test('the memory filter reflects memories loaded after the component is built', () => {
  const app = { ...loadBotMixin().chatBotMixin() };

  assert.deepStrictEqual(Array.from(app.filteredBotMemories()), []);

  app.botMemories = MEMORIES;
  assert.equal(app.filteredBotMemories().length, 3);
});
