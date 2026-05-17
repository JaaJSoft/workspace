/** @type {import('tailwindcss').Config} */
module.exports = {
  // Tailwind only ships classes it can see as literal strings in these files.
  // Anything constructed at runtime (Django `bg-{{ color }}/10`, Alpine
  // `\`badge-${tag.color}\``) is invisible to the scanner and must be in
  // the `safelist` below.
  content: [
    '../../workspace/**/templates/**/*.html',
    '../../workspace/**/static/**/*.js',
  ],
  // DaisyUI semantic colors that the codebase interpolates at runtime
  // (audit identified 14 dynamic patterns: module.color, calendar.color,
  // tag.color, cmd.color, etc.). Without this, those rules get purged.
  safelist: [
    {
      pattern: /^(bg|text|border|ring|fill|stroke|checkbox|badge|btn)-(primary|secondary|accent|neutral|info|success|warning|error|ghost|base-100|base-200|base-300|base-content)(-content)?$/,
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
      pattern: /^(bg|text|border|ring)-(primary|secondary|accent|neutral|info|success|warning|error|ghost|base-100|base-200|base-300|base-content)\/\d+$/,
      variants: ['hover'],
    },
  ],
  theme: {
    extend: {},
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('daisyui'),
  ],
  // Themes available in the user preference picker
  // (workspace/users/ui/templates/users/ui/partials/settings_preferences.html
  // lightThemes + darkThemes). Listing a theme bakes it into the bundle;
  // omitting it would break that user's selection.
  daisyui: {
    themes: [
      'light', 'cupcake', 'bumblebee', 'emerald', 'corporate', 'retro',
      'valentine', 'garden', 'pastel', 'lemonade', 'autumn', 'winter', 'nord',
      'dark', 'synthwave', 'halloween', 'forest', 'aqua', 'black', 'luxury',
      'dracula', 'business', 'night', 'coffee', 'dim', 'sunset',
    ],
    logs: false,
  },
};
