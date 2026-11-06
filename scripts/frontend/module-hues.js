// A module's identity color: a Tailwind hue, theme independent, with one
// shade for light themes and one for dark ones. Must match MODULE_HUES in
// workspace/core/module_registry.py (core.tests.test_module_colors checks).
// The light shade is 500, Tailwind's vivid step. Bright hues take 500 in
// the dark too: their 400 is too pale on a dark surface. Bright hues also
// take their 950 shade as light-theme foreground: white on their 500 falls
// below 4.5:1 contrast.
const MODULE_HUES = {
  indigo: { light: 500, dark: 400 },
  sky: { light: 500, dark: 400, content: 950 },
  emerald: { light: 500, dark: 400, content: 950 },
  teal: { light: 500, dark: 400, content: 950 },
  amber: { light: 500, dark: 500, content: 950 },
  orange: { light: 500, dark: 400, content: 950 },
  purple: { light: 500, dark: 400 },
  rose: { light: 500, dark: 400 },
  cyan: { light: 500, dark: 500, content: 950 },
  slate: { light: 500, dark: 400 },
  lime: { light: 500, dark: 500, content: 950 },
  fuchsia: { light: 500, dark: 400 },
  yellow: { light: 500, dark: 500, content: 950 },
};

// Every daisyUI theme that declares `color-scheme: dark`, read from daisyUI's
// own theme definitions so the list never has to be maintained by hand. A
// selector for a theme the bundle does not ship is inert.
const daisyThemes = require('daisyui/theme/object').default;
const DARK_THEMES = Object.keys(daisyThemes)
  .filter((name) => daisyThemes[name]['color-scheme'] === 'dark')
  .sort();

// .module-<hue> sets --module / --module-content; <body> carries the current
// module's class and any tile showing another module carries its own. The
// dark rules are descendant selectors on [data-theme], which sits on <html>,
// so they reach <body> and nested tiles alike. Without a data-theme the page
// follows the system scheme (daisyUI's --prefersdark), hence the media query.
// :root falls back to slate for pages outside any module (settings, profile).
// Base rules, not utilities: the classes only ever appear interpolated
// (`module-{{ m.color }}`), so the content scanner never sees them.
module.exports = function moduleHues({ addBase, theme }) {
  const light = {};
  const dark = {};
  for (const [hue, shade] of Object.entries(MODULE_HUES)) {
    light[`.module-${hue}`] = {
      '--module': theme(`colors.${hue}.${shade.light}`),
      '--module-content': shade.content ? theme(`colors.${hue}.${shade.content}`) : '#fff',
    };
    dark[`.module-${hue}`] = {
      '--module': theme(`colors.${hue}.${shade.dark}`),
      '--module-content': theme(`colors.${hue}.950`),
    };
  }

  // The page root under a dark scheme, then every module tile below it.
  const darkScope = (scope) => ({
    [scope]: dark['.module-slate'],
    ...Object.fromEntries(
      Object.entries(dark).map(([selector, decls]) => [`${scope} ${selector}`, decls]),
    ),
  });
  const darkThemes = `:is(${DARK_THEMES.map((name) => `[data-theme=${name}]`).join(', ')})`;

  addBase({ ':root': light['.module-slate'], ...light });
  addBase(darkScope(darkThemes));
  addBase({ '@media (prefers-color-scheme: dark)': darkScope(':root:not([data-theme])') });
};
