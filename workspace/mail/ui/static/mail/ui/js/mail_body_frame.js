/**
 * Message body frame: an email's HTML is rendered in a sandboxed iframe of
 * its own, so neither the app's classes nor a `position: fixed` style it
 * carries can reach anything outside it.
 *
 * The frame's sandbox has no allow-scripts: the document never runs code,
 * which is what makes allow-same-origin safe there - it only lets this page
 * measure the body to size the frame. Links open in a new tab, outside the
 * sandbox, and the frame's own Content-Security-Policy keeps remote images
 * (and every other fetch) from loading until the user allows them.
 */

const MAIL_FRAME_CLASS = 'mail-body-frame';

// Remote images tell the sender when, and from which address, the message
// was read. Blocked unless the user allowed them for the message or sender.
function mailFrameCsp(allowImages) {
  return [
    "default-src 'none'",
    "style-src 'unsafe-inline'",
    allowImages ? 'img-src data: https: http:' : 'img-src data:',
    "form-action 'none'",
  ].join('; ');
}

// Theme values land in the frame's <style>: keep them to what a CSS value
// can hold, so nothing closes the element.
function _cssValue(value, fallback) {
  const text = String(value || '').replace(/[<>{};]/g, '').trim();
  return text || fallback;
}

function mailFrameStyles(theme = {}) {
  const color = _cssValue(theme.color, 'CanvasText');
  const link = _cssValue(theme.link, color);
  const scheme = _cssValue(theme.colorScheme, 'normal');
  const font = _cssValue(theme.fontFamily, 'sans-serif');
  return `
:root { color-scheme: ${scheme}; color: ${color}; font-family: ${font}; font-size: 14px; }
html, body { margin: 0; padding: 0; background: transparent; }
body { display: flow-root; line-height: 1.6; word-break: break-word; overflow-wrap: break-word; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; max-width: 100%; }
td, th { padding: 4px 8px; }
a { color: ${link}; text-decoration: underline; }
blockquote {
  border-left: 3px solid color-mix(in oklab, ${link} 40%, transparent);
  padding-left: 1rem; margin-left: 0;
  color: color-mix(in oklab, ${color} 70%, transparent);
}`;
}

// The whole document the frame renders. bodyHtml is the server-sanitized
// body; the CSP meta comes first so it applies before anything in the body.
function mailFrameDocument(bodyHtml, { allowImages = false, theme = {} } = {}) {
  return '<!doctype html><html><head><meta charset="utf-8">'
    + `<meta http-equiv="Content-Security-Policy" content="${mailFrameCsp(allowImages)}">`
    + '<base target="_blank">'
    + `<style>${mailFrameStyles(theme)}</style>`
    + `</head><body>${bodyHtml || ''}</body></html>`;
}

// The page's own text color, link color and color scheme, read where the
// frame sits. A frame whose color-scheme differs from the page's gets an
// opaque backdrop, so matching it is what keeps the frame transparent.
function mailFrameTheme(el) {
  const style = getComputedStyle(el);
  return {
    color: style.color,
    link: style.getPropertyValue('--module'),
    colorScheme: style.colorScheme,
    fontFamily: style.fontFamily,
  };
}

// A theme switch repaints the page without reloading it: frames re-render
// with the new colors instead of keeping the old theme's text. Started with
// the first frame, so loading the script touches nothing.
let _mailThemeObserver = null;

function _watchTheme() {
  if (_mailThemeObserver) return;
  _mailThemeObserver = new MutationObserver(() => {
    document.querySelectorAll(`iframe.${MAIL_FRAME_CLASS}`).forEach((frame) => {
      frame.dispatchEvent(new CustomEvent('mail-theme-change'));
    });
  });
  _mailThemeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
}

window.mailFrameCsp = mailFrameCsp;
window.mailFrameDocument = mailFrameDocument;

window.mailBodyFrameMixin = function mailBodyFrameMixin() {
  return {
    mailImagesAllowed() {
      const msg = this.messageDetail;
      return !!msg && (!!msg.sender_trusted || this.imagesShownFor === msg.uuid);
    },

    renderMailBody(frame) {
      const html = this.messageDetail?.body_html || '';
      const allowImages = this.mailImagesAllowed();
      const doc = mailFrameDocument(html, {
        allowImages,
        theme: mailFrameTheme(frame.parentElement || frame),
      });
      // Assigning srcdoc reloads the frame even when nothing changed.
      if (frame.srcdoc !== doc) frame.srcdoc = doc;
    },

    // Height of the body, so the frame grows with it and the panel scrolls
    // instead of the frame. Re-run on load (after images) and on a width
    // change, which reflows the text.
    fitMailBody(frame) {
      const body = frame.contentDocument?.body;
      if (!body) return;
      frame.style.height = `${Math.ceil(body.getBoundingClientRect().height)}px`;
    },

    watchMailBody(frame) {
      _watchTheme();
      let width = frame.clientWidth;
      new ResizeObserver(() => {
        if (frame.clientWidth === width) return;
        width = frame.clientWidth;
        this.fitMailBody(frame);
      }).observe(frame);
    },

    showMailImages() {
      if (this.messageDetail) this.imagesShownFor = this.messageDetail.uuid;
    },

    async trustMailSender() {
      await this._setSenderTrust(true);
    },

    async distrustMailSender() {
      await this._setSenderTrust(false);
    },

    async _setSenderTrust(trusted) {
      const msg = this.messageDetail;
      const email = msg?.from_address?.email;
      if (!email) return;
      const res = await this._fetch('/api/v1/mail/trusted-senders', {
        method: trusted ? 'POST' : 'DELETE',
        body: { email },
      });
      if (!res.ok) return;
      msg.sender_trusted = trusted;
      if (!trusted && this.imagesShownFor === msg.uuid) this.imagesShownFor = null;
    },
  };
};

