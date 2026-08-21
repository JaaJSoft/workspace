// Second bundle, loaded on demand. Everything here serves onboarding and
// password rotation only; none of it may ever land on the unlock path, where
// the main bundle has a 75 KB gzipped budget.
import { zxcvbnAsync, zxcvbnOptions } from '@zxcvbn-ts/core';
import * as common from '@zxcvbn-ts/language-common';
import { jsPDF } from 'jspdf';

zxcvbnOptions.setOptions({ dictionary: common.dictionary, graphs: common.adjacencyGraphs });

export async function estimateStrength(password) {
  const result = await zxcvbnAsync(password);
  return { score: result.score, feedback: result.feedback };
}

// The emergency kit is the only recovery path: lose the secret_key and the
// vault is unrecoverable by design. It is generated client side so the value
// never reaches the server.
export function buildEmergencyKitPdf({ username, secretKey, createdAt }) {
  const doc = new jsPDF();
  doc.setFontSize(18);
  doc.text('Vault emergency kit', 20, 25);
  doc.setFontSize(11);
  doc.text(`Account: ${username}`, 20, 40);
  doc.text(`Created: ${createdAt}`, 20, 48);
  doc.text('Secret key:', 20, 62);
  doc.setFont('courier', 'normal');
  doc.text(secretKey, 20, 70);
  doc.setFont('helvetica', 'normal');
  doc.text(
    'Store this sheet offline. Without the secret key and your vault password,',
    20, 88
  );
  doc.text('nobody - including this server - can recover your vault.', 20, 96);
  return doc.output('blob');
}

window.VaultOnboarding = { estimateStrength, buildEmergencyKitPdf };
