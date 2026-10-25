# Translations

The interface is written in English and translated through gettext. French is
the first translated language. Each user picks their language in
**Settings → Language & region**. Translation happens one module at a time, so
until a module is covered, its pages stay in English.

## How the language is chosen

- `LANGUAGES` (`workspace/settings/base.py`) lists the languages on offer, each
  named in its own language. `LANGUAGE_CODE` (`en`) is the source language and
  the fallback.
- The choice is stored as the `core` / `language` user setting (validated
  against `LANGUAGES` by `PUT /api/v1/settings/core/language`) and read through
  `get_user_language(user)` in `workspace.users.services.settings`.
- `workspace.users.middleware.LanguageMiddleware` activates that language for
  each request and sets `Content-Language`. Anonymous pages and users who never
  picked a language get `LANGUAGE_CODE`. The browser's `Accept-Language` is
  ignored on purpose: Django ships its own French catalog for dates, form errors
  and API errors, and a French browser would otherwise get those strings in the
  middle of pages that are still English.
- `base.html` renders `<html lang="{{ LANGUAGE_CODE }}">` and loads the
  JavaScript catalog of that language in `<head>`.

Code that runs outside a request (a Celery task, a push notification, an email)
has no active language. When it produces text for a user, it wraps that work in
`translation.override(get_user_language(recipient))`.

## Where catalogs live

Each module owns its catalog, in `workspace/<module>/locale/<lang>/LC_MESSAGES/`:

- `django.po`: strings from templates and Python.
- `djangojs.po`: strings from the module's `.js` files.

Django finds them because every module is an installed app. A module's `ui`
sub-app shares the module's catalog. Keeping one catalog per module, rather
than a project-wide `locale/` in `LOCALE_PATHS`, means two modules translated
in parallel never edit the same file.

The compiled `.mo` files are committed next to their `.po`, so you can run a
checkout or build the Docker image without GNU gettext.
`core.tests.test_translation_catalogs` fails when a `.mo` no longer matches its
`.po`, or when a catalog has a fuzzy header or a fuzzy entry.

## Workflow

Extracting and compiling need GNU gettext (`apt install gettext`,
`brew install gettext`).

```bash
# Extract the module's strings into its .po files (this starts the catalog the
# first time), then compile every catalog of the module.
uv run python manage.py translations users

# Every module that already has a locale/ directory
uv run python manage.py translations

# Compile only, after editing a .po
uv run python manage.py translations users --compile-only
```

1. Mark the strings (see below).
2. Run `translations <module>`.
3. Fill in the `msgstr` entries in `workspace/<module>/locale/fr/LC_MESSAGES/*.po`.
4. Run `translations <module>` again. It is idempotent: a catalog whose
   messages did not change stays byte for byte the same.
5. Commit the `.po` and `.mo` files together.

The command wraps `makemessages` and `compilemessages`. It ignores tests,
migrations and vendored bundles, keeps file names but drops line numbers from
the location comments (so diffs only show real changes), and removes obsolete
entries.

To add a language, add it to `LANGUAGES` and run `translations` once for every
module that has a catalog.

## Marking strings

### Templates

```django
{% load i18n %}
<h1>{% translate "Settings" %}</h1>

{% blocktranslate trimmed count counter=files|length %}
  {{ counter }} file
{% plural %}
  {{ counter }} files
{% endblocktranslate %}
```

- Translate whole sentences. Never build one out of translated fragments:
  word order differs between languages.
- Use `{% blocktranslate count %}` for anything with a number in it. French has
  three plural forms in current gettext catalogs: `0` and `1`, millions
  (`1 000 000 de fichiers`), and everything else.
- A translated string that ends up inside JavaScript (an inline `<script>`, an
  Alpine attribute) goes through a variable and `|escapejs`, like any other
  value:

  ```django
  {% translate "Failed to save language" as save_failed %}
  <script>
    AppAlert.error('{{ save_failed|escapejs }}');
  </script>
  ```

### Python

- `gettext` (`from django.utils.translation import gettext as _`) in code that
  runs during a request.
- `gettext_lazy` for strings defined at import time: model `verbose_name`,
  `choices`, form and serializer labels, action labels.
- `ngettext` for counts, and named placeholders
  (`_("%(name)s joined") % {"name": name}`) so translators can reorder them.

### JavaScript

`.js` files call the globals the JavaScript catalog defines:

```js
gettext('Today');
interpolate(ngettext('%(count)s file', '%(count)s files', count), { count }, true);
```

- Pass only string literals to `gettext`: extraction reads the source, so a
  variable or a template literal is invisible to it.
- Format dates and numbers with `window.getUserLocale()` (from `localtime.js`)
  instead of `undefined`. That way they follow the interface language, refined
  to the browser's regional variant of it (`fr-CA`, `en-GB`).
- The Node test loader (`workspace/common/tests/js/loader.js`) provides
  untranslated `gettext`/`ngettext`/`interpolate` stubs, so a script under test
  reads in English. To check a translated path, pass your own `gettext` in
  `extraGlobals`.

A standalone page that does not extend `base.html` has to load the catalog
itself (`{% url 'javascript-catalog' LANGUAGE_CODE %}`) before any script that
calls `gettext`.

## What stays in English

- The changelog (`CHANGELOG.md`) and the "What's new" modal it feeds.
- Seeded demo data, and content users create.
- AI system prompts and tool descriptions: they are instructions to a model,
  not interface text.
- Log messages, management command output and API documentation.
- The admin (`django-unfold`): it has no strings of ours. Django and unfold
  ship their own catalogs, so the admin follows the user's language wherever
  those catalogs cover it.
