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

// What the kit prints, as text. Exported so the contents can be asserted
// without parsing a PDF - and used by the builder below, so the two cannot
// drift apart.
export function emergencyKitFields({ email, serverUrl, secretText, createdAt }) {
  return [
    'Vault emergency kit',
    `Account: ${email}`,
    `Server: ${serverUrl}`,
    `Created: ${createdAt}`,
    'Recovery key:',
    secretText,
    'Store this sheet offline. Together with your vault password it is the',
    'only way into your vault. Without it, nobody - not you, not the server',
    'operator - can recover what is inside.',
  ];
}

// The emergency kit is the only recovery path: lose the recovery key and the
// vault is unrecoverable by design. It is built here, in the page, so the
// value never reaches the server - a server-side PDF pipeline would break the
// whole promise, which is why test_secret_never_posted.py exists.
export function buildEmergencyKitPdf(kit) {
  const doc = new jsPDF();
  const lines = emergencyKitFields(kit);
  doc.setFontSize(18);
  doc.text(lines[0], 20, 25);
  doc.setFontSize(11);
  let y = 40;
  for (const line of lines.slice(1, 5)) {
    doc.text(line, 20, y);
    y += 8;
  }
  doc.setFont('courier', 'normal');
  doc.text(lines[5], 20, y + 4);
  doc.setFont('helvetica', 'normal');
  y += 20;
  for (const line of lines.slice(6)) {
    doc.text(line, 20, y);
    y += 8;
  }
  return doc.output('blob');
}

window.VaultOnboarding = { estimateStrength, emergencyKitFields, buildEmergencyKitPdf };
