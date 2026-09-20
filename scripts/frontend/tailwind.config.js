const daisyThemes = require('daisyui/src/theming/themes');

// Themes available in the user preference picker
// (workspace/users/ui/templates/users/ui/partials/settings_appearance.html
// lightThemes + darkThemes). Listing a theme bakes it into the bundle;
// omitting it would break that user's selection.
const THEMES = [
  'light', 'cupcake', 'bumblebee', 'emerald', 'corporate', 'retro',
  'valentine', 'garden', 'pastel', 'lemonade', 'autumn', 'winter', 'nord',
  'dark', 'synthwave', 'halloween', 'forest', 'aqua', 'black', 'luxury',
  'dracula', 'business', 'night', 'coffee', 'dim', 'sunset',
];

// A module's identity color: a Tailwind hue, theme independent, with one
// shade for light themes and one for dark ones. Must match MODULE_HUES in
// workspace/core/module_registry.py (core.tests.test_module_colors checks).
// Bright hues take 500 in the dark: their 400 is too pale on a dark surface.
const MODULE_HUES = {
  indigo: { light: 600, dark: 400 },
  sky: { light: 600, dark: 400 },
  emerald: { light: 600, dark: 400 },
  teal: { light: 600, dark: 400 },
  amber: { light: 600, dark: 500 },
  orange: { light: 600, dark: 400 },
  purple: { light: 600, dark: 400 },
  rose: { light: 600, dark: 400 },
  cyan: { light: 600, dark: 500 },
  slate: { light: 600, dark: 400 },
  lime: { light: 600, dark: 500 },
  fuchsia: { light: 600, dark: 400 },
  yellow: { light: 600, dark: 500 },
};

// Themes whose daisyUI definition declares `color-scheme: dark`: the dark
// module shade applies under any of them. Read from daisyUI so the list
// never has to be maintained by hand.
const DARK_THEMES = THEMES.filter((t) => daisyThemes[t]['color-scheme'] === 'dark');

// '#d97706' -> '217 119 6', the form `rgb(var(--module) / <alpha-value>)` needs.
function channels(hex) {
  const n = parseInt(hex.slice(1), 16);
  return `${n >> 16} ${(n >> 8) & 255} ${n & 255}`;
}

// .module-<hue> sets --module / --module-content; <body> carries the current
// module's class and any tile showing another module carries its own. The
// dark rule is a descendant selector on [data-theme], which sits on <html>,
// so it reaches <body> and nested tiles alike. :root falls back to slate for
// pages outside any module (settings, profile).
function moduleHuesPlugin({ addBase, addComponents, theme }) {
  const light = {};
  const dark = {};
  for (const [hue, shade] of Object.entries(MODULE_HUES)) {
    light[`.module-${hue}`] = {
      '--module': channels(theme(`colors.${hue}.${shade.light}`)),
      '--module-content': '255 255 255',
    };
    dark[`.module-${hue}`] = {
      '--module': channels(theme(`colors.${hue}.${shade.dark}`)),
      '--module-content': channels(theme(`colors.${hue}.950`)),
    };
  }
  const darkScope = DARK_THEMES.map((t) => `[data-theme=${t}]`).join(', ');
  const rules = { ':root': light['.module-slate'], ...light };
  rules[`:is(${darkScope})`] = dark['.module-slate'];
  for (const [selector, decls] of Object.entries(dark)) {
    rules[`:is(${darkScope}) ${selector}`] = decls;
  }
  // addComponents rather than addBase: base-layer class rules are dropped even when safelisted.
  addComponents(rules);
}

/** @type {import('tailwindcss').Config} */
module.exports = {
  // Tailwind only ships classes it can see as literal strings in these files.
  // Anything constructed at runtime (Django `bg-{{ color }}/10`, Alpine
  // `\`badge-${tag.color}\``) is invisible to the scanner and must be in
  // the `safelist` below.
  content: [
    '../../workspace/**/templates/**/*.html',
    // offline.html is a static file, not a template: the service worker
    // serves it directly, and its classes are purged without this line.
    '../../workspace/**/static/**/*.html',
    '../../workspace/**/static/**/*.js',
    // Minified vendored bundles carry no utility class, but the extractor
    // finds class-shaped substrings in them and emits dead rules for each.
    '!../../workspace/**/static/**/vendor/**',
  ],
  // DaisyUI semantic colors that the codebase interpolates at runtime
  // (calendar.color, tag.color, badge_type, drawer_item's color param...).
  // Without this, those rules get purged. `module` is in the list for
  // drawer_item.html, which interpolates `bg-{{ c }}/10` with c="module".
  safelist: [
    {
      pattern: /^(bg|text|border|ring|fill|stroke|checkbox|badge|btn|toggle|link|progress|range|radio|input)-(primary|secondary|accent|neutral|info|success|warning|error|ghost|base-100|base-200|base-300|base-content|module)(-content)?$/,
      variants: ['hover', 'focus', 'group-hover'],
    },
    {
      // Opacity-modified variants: bg-primary/10, text-warning/5, etc.
      // `hover` variant required because drawer_item.html and
      // upcoming_events.html interpolate `hover:bg-{{ c }}/X` where c
      // resolves at render time - the prefixed form never appears as a
      // literal string for the scanner to see. Other prefixes
      // (focus-within, group-hover, etc.) are only used with LITERAL
      // color names in the codebase, so the scanner already covers them.
      // `fill`/`stroke` cover the gantt chart's SVG classes, which are
      // built as plain strings in workspace/projects/services/timeline.py
      // (and any other chart service) - the scanner never reads .py files,
      // so those classes only exist in the bundle because of this entry.
      pattern: /^(bg|text|border|ring|fill|stroke)-(primary|secondary|accent|neutral|info|success|warning|error|ghost|base-100|base-200|base-300|base-content|module)\/\d+$/,
      variants: ['hover'],
    },
    {
      // Module hue classes come from moduleHuesPlugin and only ever appear
      // interpolated in templates (`module-{{ m.color }}`), so the scanner
      // never sees one as a literal and the rules would be purged.
      pattern: new RegExp(`^module-(${Object.keys(MODULE_HUES).join('|')})$`),
    },
  ],
  theme: {
    extend: {
      colors: {
        module: 'rgb(var(--module) / <alpha-value>)',
        'module-content': 'rgb(var(--module-content) / <alpha-value>)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('daisyui'),
    moduleHuesPlugin,
  ],
  daisyui: {
    themes: THEMES,
    logs: false,
  },
};
