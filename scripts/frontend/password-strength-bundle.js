// The password strength estimator, on its own. Every page that lets a user
// choose a password loads this - the account password form fetches it the
// first time the field is shown, the vault onboarding page up front - so it
// carries zxcvbn and nothing else: at ~230 KB gzipped it is already the
// heaviest thing those pages fetch, and jsPDF used to ride along with it.
import { ZxcvbnFactory } from '@zxcvbn-ts/core';
import * as common from '@zxcvbn-ts/language-common';
// The deep path is deliberate: the package index also exports the English
// dictionaries (names, Wikipedia, common words - 1.4 MB minified), and the
// common pack this bundle already carries has the ones zxcvbn ranks by.
import translations from '@zxcvbn-ts/language-en/dist/translations.mjs';

// Without the translations, feedback comes back as message keys (`topTen`,
// `straightRow`) - which is what the vault onboarding page showed for a
// while. The English pack turns them into the sentences the meter prints.
const zxcvbn = new ZxcvbnFactory({
  dictionary: common.dictionary,
  graphs: common.adjacencyGraphs,
  translations,
});

// score is zxcvbn's 0-4 band; warning is one sentence or empty; suggestions
// is a list of sentences, possibly empty. Nothing here says whether the
// password is acceptable - that is the caller's floor to set.
export async function estimateStrength(password) {
  const result = await zxcvbn.checkAsync(password);
  return {
    score: result.score,
    warning: result.feedback.warning || '',
    suggestions: result.feedback.suggestions || [],
  };
}

window.passwordStrengthTools = { estimateStrength };
