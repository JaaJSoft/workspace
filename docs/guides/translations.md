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

## Shared words

Words every module uses ("Cancel", "Save", "Close", "Name", "Tags") are
translated once, in `common`, not once per module. So is the text of
`common`'s own UI kit (dialogs, comments, the preferences popover, the
password generator...), which every module renders. Generic words that no
`common` template or script happens to use are listed in
`workspace/common/vocabulary.py`, a `gettext_noop` list nothing imports: it
exists so extraction puts them in `common`'s catalog.

`common`'s server-side catalog is also served to the browser, merged into the
JavaScript catalog, so one msgid covers templates, Python and scripts:
`gettext('Cancel')` in any `.js` file finds the same translation as
`{% translate "Cancel" %}`.

`translations <module>` hands `common`'s catalogs to
`xgettext --exclude-file`, so a module that uses a shared msgid gets no entry
of its own and the lookup lands on `common`'s translation (Django merges every
installed app's catalog into one):

- a module's `django.po` leaves out what `common`'s `django.po` holds;
- a module's `djangojs.po` leaves out what both of `common`'s catalogs hold,
  since the browser reads both;
- `common`'s own `djangojs.po` leaves out what its `django.po` holds, so
  `timeago.js` and `dates.py` share one "%(count)sm ago".

A catalog that repeats a shared msgid fails
`core.tests.test_translation_catalogs`.

- Before translating a generic word in a module, add it to the vocabulary,
  run `translations common`, then `translations <module>` to drop it from the
  module.
- A word whose meaning depends on the page takes a context at its call site
  (`{% translate "Back" context "history navigation" %}`,
  `pgettext('table column', 'Select')`): a msgid with a context is a
  different message, so it stays in the module. The back button of a
  back/forward pair reads "Précédent", not "Retour"; a checkbox column reads
  "Sélection", not "Sélectionner".
- An adjective whose French agrees with its noun ("Created", "New", "All",
  "Recent") stays out of the vocabulary: "Créé" or "Créée" depends on what
  was created.
- After a run that moved msgids to `common`, compare what left the module
  with `common`'s translation: a word that read differently in the module is
  one that needs a context.

## Catalogs that shadow ours

Django merges every installed app's catalog, and an app listed earlier in
`INSTALLED_APPS` wins a msgid both hold. Django's and DRF's own catalogs come
first, so a common msgid can come out in their words rather than ours: "Page
not found" reads "Page non trouvée", "View" reads "Afficher", "Created" reads
"Création". Give such a msgid a context at its call site
(`{% translate "View" context "activity item" %}`).
`core.tests.test_translation_catalogs` resolves every translated msgid under
its language, through gettext for server-side catalogs and through the
JavaScript catalog view for script catalogs, and fails on any that comes out
differently.

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
  (`1 000 000 de fichiers`), and everything else. `makemessages` only writes
  two `msgstr[]` slots, so add the third by hand: a missing form compiles fine
  and shows the English string for every count that selects it.
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

- The product name, Workspace, in every language.
- The changelog (`CHANGELOG.md`) and the "What's new" modal it feeds.
- Seeded demo data, and content users create.
- AI system prompts and tool descriptions: they are instructions to a model,
  not interface text.
- Log messages, management command output and API documentation.
- The offline page (`core/static/offline.html`) and the service worker's
  fallback notification title: the service worker serves them as they are,
  with no template engine and no catalog.
- The admin (`django-unfold`): it has no strings of ours. Django and unfold
  ship their own catalogs, so the admin follows the user's language wherever
  those catalogs cover it.
