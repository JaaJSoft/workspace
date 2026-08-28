# Claude Code Instructions

## Commands

```bash
# Setup
uv sync                                        # install dependencies
uv run python manage.py migrate                # apply migrations
uv run python manage.py runserver 127.0.0.1:$port --noreload   # NEVER :8000 in agent sessions - pick a free port, see .claude/skills/running-the-app

# Tests (per module - matches CI matrix)
uv run python manage.py test workspace.<module>           # e.g. workspace.files
uv run coverage run manage.py test workspace.<module>     # with coverage

# Async stack
uv run celery -A workspace worker -l info
uv run celery -A workspace beat -l info

# Vendored frontend assets - Alpine bundle, Lucide icons, Milkdown editor
# bundle + theme CSS, vault crypto bundles, Tailwind stylesheet (rebuild after
# bumping any dependency in scripts/frontend/package.json; templates load the
# built artifacts, never a CDN)
cd scripts/frontend && npm run build
cd scripts/frontend && npm run build:css      # Tailwind only, after template/JS class changes
# Vault crypto only - the main bundle has a 75 KB gzipped budget enforced by
# workspace/vault/tests/js/vault_bundle.test.js
cd scripts/frontend && npm run build:vault && npm run build:vault-onboarding
```

## Module Map

Each Django app under `workspace/` follows the same shape (`models.py`, `views.py` or `views/`, `services/`, `tests/`, `ui/`, `urls.py`):

| Module | Purpose |
|---|---|
| `ai` | LLM tools, AI assistants, prompt routing |
| `calendar` | Events, recurrence, external calendar sync |
| `chat` | Conversations, messages, typing indicators, link previews |
| `common` | Cross-cutting helpers: UUIDs, booleans, logging, cache, mixins |
| `core` | Auth, navigation, changelog, dashboard scaffolding |
| `dashboard` | User home page widgets |
| `files` | File/folder model, permissions, WebDAV, thumbnails, sharing |
| `imports` | Import data from other clouds (WebDAV/Nextcloud, later OAuth drives) |
| `mail` | IMAP/SMTP, OAuth2 providers, labels, autodiscover |
| `notes` | Markdown notes built on the files module |
| `notifications` | Web push, in-app notifications |
| `projects` | Projects and kanban boards: tasks, statuses, members, comments, task references |
| `users` | User model, settings, profile, activity feed |
| `vault` | End-to-end encrypted password vault (preview) |

## Infrastructure

- **Cache & sessions:** Redis (`django-redis`). Sessions are NOT in the DB in production - don't count `SELECT django_session` as a prod cost.
- **Async tasks:** Celery + Redis broker. Background work (mail sync, thumbnails, push) runs via tasks; never block a request on it.
- **Database:** PostgreSQL canonical, SQLite for dev/tests (see `core/management/commands/sqlite_to_postgres.py` for migration).

## Workflow

### Git

- Committing on your own initiative (without being asked) is fine, as long as the current branch is not `master`/`main`.
- **Forbidden: commit to `master`/`main`.** These branches are protected - no direct commits ever, even if explicitly requested. If we're on `master`/`main` and a commit is warranted, create a feature branch first (`type/short-subject`, e.g. `feat/theme-picker`) and commit there. **Committing to any other branch has no restrictions.**
- Git worktrees are allowed - use one when isolating work from the current workspace is useful. Otherwise work directly on the current branch (creating a feature branch when the current branch is `master`/`main`, per the rule above).
- Never mention "Claude", "Claude Code", "CLAUDE.md", or any AI/assistant attribution in commit messages, commit titles, PR titles, or PR descriptions. The user wants commits and PRs to read as if a human wrote them. This includes the trailing "🤖 Generated with [Claude Code]" footer and the "Co-Authored-By: Claude" trailer - omit both. References to project rules should cite the rule itself ("per the no-logic-change refactor contract"), not the file ("per CLAUDE.md").
- All commit messages **and** PR titles must follow the Conventional Commits format `type(scope): subject` (e.g. `feat(theme): split theme picker into light and dark slots`, `fix(chat): prevent duplicate retry`). Allowed types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `style`, `build`, `ci`, `revert`. Subject is lowercase, imperative mood, no trailing period. This applies to PR titles too - don't pass a free-form title to `gh pr create`, prefix it.

### Issues

Issues use GitHub's native **issue type** field, not a label. Conventional Commits stay on commits and PR titles - an issue title never carries a `type(scope):` prefix, because the type field and the `module:*` labels already encode both halves of it.

- **Type** (mandatory, one of three): `Bug` for something that behaves differently from what it should, `Feature` for a capability that doesn't exist yet, `Task` for everything else - refactor, perf, chore, docs, test, build, ci. Set it with `gh issue edit <n> --type Bug`; the templates in `.github/ISSUE_TEMPLATE/` set it automatically for issues opened from the web UI.
- **Title**: `Module: Description`, e.g. `Files: Support S3-compatible object storage`, `Mail: The Sent copy of a message drops its Bcc recipients`. The prefix is the module name capitalized (`AI` for `ai`, `Build` for tooling that isn't a Django app); the description is a sentence, capitalized, no trailing period.
- **One module in the title - the main one.** An issue spanning `projects` and `calendar` reads `Projects: ...`; the fact that it also touches the calendar lives in a `module:calendar` label. The labels are the source of truth for the full scope, the title prefix is a navigation aid. Never widen the prefix into `Projects, Calendar: ...`.
- **Labels**: one `module:*` per module actually touched. Never re-introduce `type:*` labels - they were deleted precisely because they duplicated the type field and drifted from it.
- Opening an issue from the CLI bypasses the template's prompts, so the type, the `Module:` title prefix and the `module:*` label all have to be passed by hand - none of them is inferred: `gh issue create --type Feature --title "Files: Support S3-compatible object storage" --label module:files --body "..."`. `gh issue create --template Feature` (the template's display name, not its filename) only seeds the body, so it still needs `--type`, `--title` and `--label`.

### PR Descriptions

PR descriptions must follow the structure of `.github/PULL_REQUEST_TEMPLATE.md` (Summary / Changes / Screenshots / Testing / Notes). GitHub only pre-fills that template when no body is provided, and `gh pr create --body` bypasses it - so when writing a body, reproduce the structure manually. Rules:

- Write for a human reviewer and for whoever reads the PR in a year. Explain intent and impact; never paraphrase the diff line by line - the diff is right below.
- `Summary` is mandatory: 2-5 sentences, outcome first, approach only if non-obvious.
- Every other section is optional - **delete** sections that don't apply, never leave empty headings or "N/A". Small PRs often need only `Summary` and `Testing`.
- `Changes` lists only what a reviewer needs to navigate the diff. Omit mechanical fallout (renames, import updates, generated files, "updated tests accordingly") and process narration ("verified X still works", "explored Y before choosing Z").
- `Testing` is factual: suites run with results, what new regression tests pin down, manual checks performed. No checklists, no ✅ theater.
- `Notes` only when there is real content: breaking changes, known limitations, deliberate follow-ups, review focus areas.
- The PR is the project's public face: no internal tooling details, no self-congratulation, and (per the attribution rule above) nothing that reads as machine-generated.

#### Screenshots are mandatory for any UI change

An agent working unattended has no one looking over its shoulder: the reviewer sees the diff, not the running app. Any PR that touches what a user sees (templates, Tailwind/daisyUI classes, Alpine components, JS that changes rendering, new pages or dialogs) **must** carry screenshots of the real result in the `Screenshots` section - not a description of what it "should look like", not a Playwright test that passed. No screenshot means the change is unverified, and the reviewer will send it back. Pure backend PRs (models, services, API, Celery, tests, CI) drop the section entirely, as usual.

**What to shoot:** the changed surface in its final state, at the desktop viewport (1280x900) and, when the layout is responsive, a mobile-width capture too. Before/after pairs when the PR fixes something visual. Both light and dark theme when the change involves colours or theming - the theme is a per-user setting, so switch it with `set_setting(user, 'core', 'theme', 'dark')` (or `PUT /api/v1/settings/core/theme`) between the two runs. Crop nothing that a reviewer would need to judge the surrounding layout.

**How to produce them** (all steps run locally, no external tool):

1. Boot the app on a free port with seeded data - the `seeding-demo-data` and `running-the-app` skills cover both (`demo` / `demo1234`, never port 8000).
2. Drive headless Chromium with Playwright, already in the `dev` dependency group (`uv run playwright install chromium` once). Log in through `/login`, navigate to the changed page, wait for the network to settle, then `page.screenshot(path=..., full_page=False)`. `scripts/screenshots.py` (`capture()`, `_dismiss_overlays()`, `CONTEXT_OPTIONS`) is the reference implementation to crib from - it pins the viewport, locale and timezone so captures don't depend on the host machine.
3. **Look at each capture before attaching it.** A screenshot of a blank page, an error toast, or a modal that never opened proves the opposite of what the PR claims - and it is exactly what a naive script produces when a selector was wrong. Open the PNG (Read tool) and check it shows the feature working.

**How to attach them** - GitHub offers no CLI upload for PR-body images, and committing PNGs into the PR pollutes the tree forever, so every screenshot goes on the shared orphan branch **`pr-assets`** (never merged). It holds exactly one directory per pull request, named after the PR number - `pr-702/`, `pr-703/`, ... - nothing else: no issue numbers, no branch names, no free-form slugs. A flat, monotonically growing list is what makes it trivial to find a PR's captures a year later and to prune everything below a given number. That means the PR exists **before** the captures are pushed: open it first (`gh pr create` prints the URL, so the number is known), push the images under `pr-<n>/`, then add the links with `gh pr edit <n> --body-file`, keeping the rest of the body intact.

Add to the branch through a temporary worktree - never `git checkout pr-assets` in your working copy, and never `git checkout --orphan` there either (cleaning that up wipes `.venv`, `db.sqlite3` and `.env`):

```bash
shots=/tmp/pr-shots            # write the captures here, outside the repo, so they never show up in git status
n=<pr number>
git fetch -q origin pr-assets && git worktree add -q /tmp/pr-assets-wt origin/pr-assets
mkdir -p /tmp/pr-assets-wt/pr-$n && cp "$shots"/*.png /tmp/pr-assets-wt/pr-$n/
git -C /tmp/pr-assets-wt add . && git -C /tmp/pr-assets-wt commit -qm "chore(assets): screenshots for #$n"
git -C /tmp/pr-assets-wt push -q origin HEAD:pr-assets || { git -C /tmp/pr-assets-wt pull -q --rebase origin pr-assets && git -C /tmp/pr-assets-wt push -q origin HEAD:pr-assets; }
git worktree remove --force /tmp/pr-assets-wt
```

The `|| pull --rebase` handles another agent having pushed to `pr-assets` in the meantime - directories never overlap, so the rebase is always clean. Reference each image in the PR body as `https://raw.githubusercontent.com/<owner>/<repo>/pr-assets/pr-<n>/<file>.png` (with `![caption](url)`, or `<img src=... width=...>` for side-by-side mobile shots). Never commit the PNGs to the feature branch itself, and never link to a path under `docs/` or `.github/` on a feature-branch commit - the link dies when the branch is deleted after merge, and the file lands in `main` for nothing.

### Backward Compatibility

By default, do not preserve backward compatibility. In doubt, ask the user.

Rationale: APIs, data formats, query parameters, and behavior are not semver'd internally - no callers outside this project depend on stable contracts. Shipping a breaking change (renamed field, removed endpoint, modified response shape, stricter validation) is the right call if it simplifies the code or model: update all call sites and remove the old path - no legacy aliases, shims, deprecated parameters, or dual code paths kept around "just in case". Only preserve compatibility when:

- The change would require a migration strategy (data export/re-import, user-facing schema changes).
- A production system or external integration actively depends on the old interface.
- You're explicitly unsure whether a caller exists - ask the user before breaking it.

When in doubt between "the old way is dead code" and "someone might use this," ask rather than guessing.

### Refactoring & Optimization

Before any refactor or optimization, verify that at least one test covers the code being touched. If no test exists, **write the test first** (it must pass against the current code), then start the refactor. The test acts as a safety net to guarantee the behavior is preserved.

### Bug Fixes

Every bug fix must ship with a regression test. Write the test alongside the fix and **verify it fails against the buggy code** (e.g. by stashing the fix, running the test, then re-applying), so you have evidence the test actually pins the bug down rather than accidentally passing for unrelated reasons. Without this proof the test is decorative: a future regression of the same bug would slip through CI. The test belongs in the same module's `tests/` package as the code being fixed.

**Exception - purely visual/CSS fixes don't get a unit test.** This rule targets *behavioral* bugs (backend logic, parsing, permissions, data handling). For a fix that only changes presentation (Tailwind/daisyUI classes, template markup, spacing, alignment, responsive layout) with no change in behavior, **do not** add a test that asserts CSS class strings are present in rendered HTML (`assertIn('h-auto', html)`). Such tests are worthless: they re-encode the template's class list at the same level of abstraction, they pass even when the layout is visually broken (a class string being present proves nothing about how it renders), and they break on any equally-correct restyle. Validate visual fixes by eye (or a real browser/Playwright rendering test that checks computed geometry if a genuine safety net is warranted) - never by class-presence assertions. Recompiling the CSS bundle after class changes is still required.

### Code Comments

Comment only when it helps someone understand the code when re-reading it cold in 6 months - never to explain the change to the PR reviewer. "Now uses X", "moved from Y", "replaces the old Z", justifications of why the change is correct: that context belongs in the commit message and PR description, and becomes noise the moment the PR merges. Always prefer making the code self-explanatory through variable and function names over adding a comment; a comment that paraphrases what the code already says is noise to delete. The comments worth writing state what the code cannot: an invariant, a non-obvious constraint, a gotcha, why the seemingly simpler approach doesn't work.

### Changelog

`CHANGELOG.md` is written for **end users**, not developers. Each release describes what changed from the user's perspective, in plain language.

**Structure of a release entry:**

1. `## <version> - <title>` heading. The title is a short thematic label (2-4 words) summarizing the release theme: *Performance & Reliability*, *Calendar Overhaul*, *Profile & Rich Media*. It shows up next to the version number in the in-app "What's new" modal. Em-dash (`-`), en-dash (`-`), hyphen (`-`), and colon (`:`) are all accepted as separators; the title is optional but recommended for non-patch releases.
2. `### Highlights` - 1-2 punchy sentences selling the release: what users will notice, phrased to make the update feel worth installing, without overselling. Keep it short relative to the sections below (those carry the detail). No bullet list here.
3. Then one `###` section per user-facing area (module name or feature theme: *Chat*, *Files & Notes*, *Calendar*, *WebDAV*, *Profile & UI*, *Performance*, *API Tokens*, *Fixes*, …).

**What to include:** new features, visible improvements, behavior changes, user-visible bug fixes, performance gains phrased as *"faster X"* / *"quicker Y"*, new integrations or endpoints that users can call.

**What to exclude (do not write these in the changelog):**
- Refactors with no visible effect (`services.py` → `services/` package, extracting helpers, centralizing logic, moving code between modules)
- Internal test additions, coverage thresholds, CI changes
- Documentation-only changes (including CLAUDE.md updates)
- Dependency bumps, unless they bring a user-visible feature or fix
- Implementation details: library names (Knox, alpine-ajax, Celery…), query patterns (N+1, `bulk_update`, composite indexes, prefetch), internal APIs (`FileService.X`, `ActionRegistry`, `$ajax`), framework-specific terms (`transaction.atomic`, `x-target`, serializer fields)

**Tone:** describe the outcome, not the mechanism. ✅ *"Faster conversation listings"* ❌ *"Added composite index on `conversation_member(user_id, left_at)`"*. ✅ *"Large uploads are more reliable on slow networks"* ❌ *"Streamed WebDAV PUT for TCP backpressure"*.

**Process:** when preparing a release, read commits since the last tag (`git log v<last>..HEAD --oneline`), group them by user-facing theme, then translate each group into one bullet the user can understand. Commits that map to nothing user-visible are dropped - not every commit deserves an entry.

## Testing

### Structure

Every module must have its tests inside a `tests/` package (directory with `__init__.py`), **not** a single `tests.py` file. Test files must follow the `test_*.py` naming convention (never `tests_*.py`).

```
workspace/<module>/tests/
├── __init__.py
├── test_models.py
├── test_views.py
└── ...
```

### Media root

`MEDIA_ROOT` defaults to `BASE_DIR`, so a test that saves file content would write real blobs into the checkout under `files/users/<username>/` - a path `.gitignore` hides, so nothing surfaces it. `TEST_RUNNER` (`workspace/test_runner.py`) redirects the media root to a temporary directory for the whole run and removes it afterwards, so **no test needs its own `MEDIA_ROOT` override to stay out of the tree**. Never re-introduce the `override_settings(MEDIA_ROOT=tempfile.mkdtemp())` idiom for that purpose; write content and read `settings.MEDIA_ROOT` when a path is needed.

Under `--parallel` each worker gets its own `worker-<pid>/` subdirectory of that root: Django clones the database per worker but never the media tree, and storage paths are built from fixture names that repeat across test classes (`files/users/alice/`, `files/groups/Team/`), so a shared root lets one worker overwrite a blob another is asserting on.

The one case that still warrants an isolated root is a test that **walks** the media tree - a sync reconciliation asserting how many nodes it created, a purge command deleting every blob no row points at. A rolled-back transaction takes the rows away but leaves the files, so those tests would see earlier tests' leftovers. Use `IsolatedMediaRootMixin` from `workspace/common/tests/media.py` (mixed in before the `TestCase` base, `super().setUp()` first); it exposes the fresh directory as `self.media_root`.

### CI

Tests run in parallel in CI with one job per module (see `.github/workflows/tests.yml`), in three separate matrices: `test` (Django), `e2e` (Playwright) and `js` (Node). When creating a new Django app module, add it to the `test` matrix; add it to the `e2e` and `js` matrices as soon as it grows a `tests/e2e/` or `tests/js/` case. `core.tests.test_ci_workflow` fails when either of those two lists drifts from the tree - a module missing from a matrix is never run, silently.

**CI coverage floors** (`.github/workflows/tests.yml`): each module pins a `min_coverage` (45-95%). Lowering a threshold is forbidden by the workflow's own comment - raise it after adding coverage, never lower it.

**CI only runs what a change can affect.** Every workflow carries a `paths` filter, so docs-only commits (`*.md` except `CHANGELOG.md`, `docs/`, issue/PR templates, `.claude/`) start no run at all, and the Docker workflow skips anything `.dockerignore` keeps out of the image (tests, docs); the Trivy workflow only runs on its nightly schedule (per-commit scanning lives in the Docker workflow). Inside `tests.yml` a `changes` job (`dorny/paths-filter`) further gates the narrow jobs: `lint` needs a Python/`pyproject.toml`/`uv.lock` change, `djlint` a `workspace/**.html` or manifest change, `js` a `workspace/**.js` change, `webdav-mount` a change under `files`/`common`/`users`/`core`/`settings` or the dependency manifests. The Django and E2E matrices always run once the workflow triggers - they render templates and read static assets, so almost any change can reach them. When a job grows a new dependency (e.g. the mount test starts importing from `mail`), extend its filter in the `changes` job; when in doubt, err on running the job.

### JS unit tests

Frontend helpers are tested with Node's built-in test runner - no npm dependencies, no package.json:

```bash
node --test "workspace/*/tests/js/**/*.test.js"    # Node >= 22
```

- Test files live in `workspace/<module>/tests/js/<name>.test.js`, next to the module's Python tests. No `__init__.py` in `js/`, so Django test discovery ignores it.
- Production JS files are classic scripts (globals + `window.X = ...`), not ES modules - they can't be `require()`d. Load them through the shared loader (`workspace/common/tests/js/loader.js`), which executes the file in a `node:vm` context with browser-like `window === globalThis` semantics and returns the context:

```js
const { loadScript } = require('../../../common/tests/js/loader');
const ctx = loadScript('workspace/common/static/ui/js/uuid.js');
assert.equal(ctx.isValidUuid('...'), true);
```

- Only top-level `function`/`var` declarations and `window.X` assignments are reachable on the returned context; top-level `const`/`let` are not (global lexical scope). Test the public surface.
- If a script touches `document`/`fetch` at load time, pass stubs: `loadScript(path, { document: stub })`.
- **Cross-realm gotcha:** arrays/objects created inside the vm carry that realm's prototypes, so `assert.deepStrictEqual` fails its prototype check against test-side literals ("same structure but not reference-equal"). Normalize first: `Array.from(ctx.fn(...))` or `{ ...result }`.
- **Cross-realm, the dangerous half:** it is not only assertions. A value built on the test side and passed *into* the vm carries the outer realm's prototypes, and a bundled library that branches on `constructor === Array` (or any other identity check against a built-in) then takes a different path than it would in a browser — same input, different output, silently. We hit this with `cbor-x`, which fell through to its iterator branch and emitted indefinite-length CBOR arrays that a browser encodes with a definite length; the suite reported 142 failures that did not exist in production. **When the test feeds data to the bundle, build that data inside the vm:** pass the JSON as a string through `extraGlobals` and parse it in the context (`vm.runInContext('JSON.parse(__text)', ctx)`), rather than parsing on the test side. If the output has to match a browser byte for byte, assert it in a real browser too — see `workspace/vault/tests/e2e/test_crypto_browser.py`.
- CI runs these in the per-module `js` matrix of `.github/workflows/tests.yml`, one `JS (<module>)` job each.

## Backend Conventions

### Settings

Project settings live in the `workspace/settings/` **package**, one module per topic (`base`, `security`, `apps`, `middleware`, `templates`, `db`, `cache`, `api`, `monitoring`, `storage`, `files`, `imports`, `chat`, `mail`, `notifications`, `ai`, `celery`, `debug_toolbar`). `__init__.py` only star-imports those modules - that is the mechanism Django uses to read settings as attributes of `workspace.settings`, and the one sanctioned exception to the no-re-export rule.

- Add a new setting to the module that owns its topic, **never** to `__init__.py`. A new module must be added to the star-import list; `workspace/core/tests/test_settings_layout.py` fails if it isn't.
- Each setting is assigned in exactly one module (the same test enforces it), so import order carries no meaning - a module needing a value from another one imports it explicitly (`from .base import DEBUG`).
- Values that are wiring rather than settings (derived Redis URLs, env scratch variables) are `_`-prefixed so the star import keeps them out of `django.conf.settings`.
- Read env vars through `env_bool` / `env_list` from `workspace/settings/env.py`; importing that module is also what loads `.env`.

### API

All API endpoints must be prefixed with `/api/` and have no trailing slashes.

### Models

Every model must use a UUID primary key with the `uuid_v7_or_v4` helper as default. Never use Django's auto-incremented `id` or `uuid.uuid4` directly.

```python
from workspace.common.uuids import uuid_v7_or_v4

class MyModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
```

### Services

Business logic that doesn't belong in views, models, or tasks lives in **services**. Services are reusable across views, REST endpoints, Celery tasks, and management commands.

#### Layout

Every module exposes its services through a `services/` **package** (directory with `__init__.py`), never a single `services.py` file:

```
workspace/<module>/
├── services/
│   ├── __init__.py    # empty - DO NOT re-export
│   ├── <name1>.py
│   └── <name2>.py
├── tests/
│   ├── test_<name1>.py
│   └── test_<name2>.py
└── ...
```

Examples in the codebase: `files/services/{files,mime,thumbnails,sharing,events}.py`, `chat/services/{conversations,notifications,rendering,avatar,typing,link_preview}.py`, `mail/services/{imap_connection,imap_folders,imap_mailbox,imap_messages,imap_parse,imap_sync,label_counts,smtp,oauth2}.py`.

#### Naming rules

- File names describe **what the file contains** (a feature, an entity, an integration) - they **never contain the word "service"**. ✅ `chat/services/conversations.py` ❌ `chat/services/conversation_service.py`
- One distinct concern per file. If a single file mixes 3+ unrelated topics (membership / notifications / rendering), split it.
- Tests follow the same naming: `tests/test_<name>.py` - never `tests/test_<name>_service.py`.

#### Imports

- Default: import from the explicit submodule - `from workspace.<module>.services.<name> import X`. Keep `__init__.py` empty.
- Re-exports in `__init__.py` are allowed **only** for a canonical class/value that defines the module's core entity (e.g., `FileService` in `files/services/__init__.py`). Never re-export functions you patch in tests - `@patch('workspace.X.services.fn')` would patch the alias in `__init__`, not the call site, and silently do nothing.
- Relative imports inside a service file must escape the `services/` package with `..`:
  ```python
  # In workspace/chat/services/conversations.py
  from ..models import Conversation, ConversationMember   # ✅
  from .models import Conversation                        # ❌ resolves to services/models - doesn't exist
  ```
- For unavoidable package-style imports (`from workspace.X import old_name_service`), alias to keep call sites unchanged:
  ```python
  from workspace.users.services import settings as settings_service
  ```
  Use this only when many call sites read `settings_service.X` and renaming all of them is out of scope.

#### Test patches

`@patch('workspace.<module>.services.<name>.symbol')` patches the symbol at its **definition site**. Patch there, not at a re-export alias - patches at an alias site bind a different name and the actual call site keeps running unmocked.

### Views - one file while it fits, a `views/` package once it doesn't

A module starts with a flat `views.py`. The moment it needs a second view module, it becomes a
`views/` **package** with an empty `__init__.py` - never a sprawl of `views_<topic>.py` siblings at
the module root. `chat`, `mail`, `files`, `core`, `projects` and `calendar` are already there;
`vault` still has the flat pair and will convert when it grows a third.

```
workspace/<module>/
├── views/
│   ├── __init__.py    # empty - DO NOT re-export
│   ├── <topic1>.py
│   └── <topic2>.py
└── urls.py
```

- File names are the topic alone, never prefixed: `chat/views/messages.py`, not
  `chat/views/views_messages.py` and not `chat/views_messages.py`.
- The module's original `views.py` gets a name too, after what it actually holds
  (`chat/views/conversations.py`, `mail/views/accounts.py`, `files/views/files.py`). A file called
  `views/views.py` is never the answer.
- `urls.py` imports the submodules, not the package: `from .views import messages, pins`.
- Moving a view one directory deeper changes every relative import inside it - `from .models import X`
  becomes `from ..models import X`, including the lazy ones inside function bodies. Cross-app imports
  that were `from ..common.x import y` become absolute `from workspace.common.x import y` rather than
  growing a third dot.
- Sibling views import each other by their real module (`from .conversations import _trigger_bot_response`).
  `from ..views import ...` would resolve to the empty `__init__.py` and fail.
- The same applies to `@patch` targets: `workspace.chat.views.conversations._trigger_bot_response`,
  never `workspace.chat.views._trigger_bot_response`. The latter raises `AttributeError` against the
  empty package, so a stale patch string fails loudly rather than silently - but only if a test
  exercises it.

### Re-exports - ask before adding

Re-exporting a symbol (via `__all__`, a top-level `from .x import y` whose only purpose is to surface `y` from a different module, or any other indirection that lets a caller `from workspace.A import X` when `X` is actually defined in `workspace.B`) creates a "where is this defined?" maze. It also breaks `@patch` at the call site (see *Test patches* above) and makes refactors that move the definition silently leak the old path.

**Never introduce a new re-export - even to preserve a single test import, even to keep a constant reachable from where it used to live - without explicit user approval.** Default: update the call sites (including tests) to import from the definition module directly. When you genuinely think a re-export is warranted (e.g. a canonical class that defines the module's core entity), say so and ask before adding it.

### Access Control Querysets

Never duplicate access/permission querysets. Always use the centralized helpers listed below. Each module exposes its access control logic through its `services/` package or a `queries.py` module. This ensures permission logic is defined once per module and stays consistent across views, API endpoints, and background tasks.

**Rules:**
- Never write raw ORM filters to check access rights (e.g. `File.objects.filter(owner=user)`) - always call the corresponding helper.
- When adding a new view or API endpoint, import and use the existing helper rather than reimplementing the logic.
- If a module doesn't have a helper yet, create one in its `services/` package or `queries.py` and use it everywhere.

#### Chat - `workspace.chat.services.conversations`

```python
from workspace.chat.services.conversations import user_conversation_ids, get_active_membership

conv_ids = user_conversation_ids(user)  # returns queryset of conversation UUIDs

# Single-conversation access check - returns ConversationMember or None:
membership = get_active_membership(user, conversation_id)
```

- `user_conversation_ids`: returns conversation UUIDs where the user is an active member (`left_at__isnull=True`).
- `get_active_membership`: returns the active `ConversationMember` for a specific conversation, or `None`. Use this for per-view access checks.

#### Mail - `workspace.mail.queries`

```python
from workspace.mail.queries import user_account_ids
account_ids = user_account_ids(user)  # returns queryset of account UUIDs
```

Returns mail account UUIDs owned by the user. Use for filtering messages: `MailMessage.objects.filter(account_id__in=account_ids, ...)`.

#### Calendar - `workspace.calendar.queries`

```python
from workspace.calendar.queries import visible_calendar_ids, visible_calendars, visible_events_q

# For calendar-level queries - all visible IDs (owned incl. external + subscribed):
cal_ids = visible_calendar_ids(user)

# For UI display - split owned (excl. external) / subscribed querysets:
owned, subscribed = visible_calendars(user)

# For event-level queries (owned calendars + subscribed calendars + event membership):
events = Event.objects.filter(visible_events_q(user), title__icontains=query)
```

#### Files - `workspace.files.services.FileService`

```python
from workspace.files.services import FileService

# All accessible files (owned + group + shared) - returns Q filter, does NOT filter deleted_at:
q = FileService.accessible_files_q(user)

# Personal files only (owned, non-deleted, no group):
qs = FileService.user_files_qs(user)

# Group files only (non-deleted, from user's groups):
qs = FileService.user_group_files_qs(user)

# Single-file permission check - returns FilePermission (MANAGE/EDIT/WRITE/VIEW) or None:
perm = FileService.get_permission(user, file_obj)

# Quick boolean access check:
if FileService.can_access(user, file_obj):
    ...
```

#### Projects - `workspace.projects.queries`

```python
from workspace.projects.queries import get_project_role, project_users, user_project_ids

# Projects the user can access (active membership OR attached auth.Group):
project_ids = user_project_ids(user)

# Admin-only narrowing (group access never grants admin):
admin_ids = user_project_ids(user, role='admin')

# Single-project role check - returns 'admin', 'member', or None:
role = get_project_role(user, project)

# Reverse direction - all users who can access a project (members + group members):
users = project_users(project)
```

Task-level queries filter with `project_id__in=user_project_ids(user)` - see `tasks_due_between` / `assigned_open_tasks` in the same module for the canonical pattern (they also exclude archived projects and done statuses).

#### Vault - `workspace.vault.queries`

```python
from workspace.vault.models import VaultEntry
from workspace.vault.queries import (
    accessible_entries_q, active_identity, get_vault_role, user_vault_ids, visible_folders, visible_tags,
)

vault_ids = user_vault_ids(user)              # vaults the user can open
role = get_vault_role(user, vault)            # 'owner' | 'member' | None
qs = VaultEntry.objects.filter(accessible_entries_q(user))  # does NOT filter deleted_at
folders = visible_folders(user, vault)        # empty queryset when the vault is out of reach
tags = visible_tags(user, vault)              # empty queryset when the vault is out of reach
identity = active_identity(user)              # the user's finished AccountIdentity, or None
```

`accessible_entries_q` does not filter `deleted_at` - the trash is a legitimate view, and the caller decides.

`active_identity` excludes a pending `AccountIdentity`: `init` created the row but the browser never came back with the sealed private keys, so the account can seal nothing and open nothing yet.

### User Settings - always go through `workspace.users.services.settings`

Per-user preferences live in the `UserSetting(user, module, key, value)` model and are wrapped by service helpers that maintain a **5-minute cache** on reads and **invalidate that cache on every write**. Never touch `UserSetting.objects` directly from views, serializers, tasks, or other services - the cache will go stale and subsequent reads will silently return the previous value until the TTL expires or the process restarts.

```python
from workspace.users.services.settings import (
    get_setting, set_setting, delete_setting, get_module_settings,
)

# Read with default (cached, 5-min TTL):
show = get_setting(user, 'dashboard', 'show_upcoming_events', default=True)

# Write (updates DB AND invalidates cache):
set_setting(user, 'dashboard', 'show_upcoming_events', False)

# Delete (DB row removed AND cache invalidated):
delete_setting(user, 'dashboard', 'show_upcoming_events')

# Read all keys for a module at once (cached 5 min, invalidated on any set/delete in that module):
prefs = get_module_settings(user, 'dashboard')
```

**Rules:**
- Never call `UserSetting.objects.create/update/delete/update_or_create` from application code - use `set_setting`/`delete_setting` instead. Raw ORM bypasses the cache invalidation and causes "F5 reverts my setting" bugs.
- The REST endpoint `PUT/DELETE /api/v1/settings/<module>/<key>` already delegates to these helpers - new UI that toggles a setting should just call it (fire-and-forget `fetch` is the idiom, see `themePickerForm()` in `settings_appearance.html` and `dashboardPrefsForm()` in `dashboard/index.html`).
- In tests that call `set_setting`/`delete_setting`, **always `cache.clear()` in `tearDown`**. Django's `LocMemCache` is process-global and is NOT reset between `TestCase` runs, so leaked cache entries can cause order-dependent failures.

```python
from django.core.cache import cache

class MyTests(TestCase):
    def tearDown(self):
        cache.clear()
```

### Logging - sanitize user-controlled values with `scrub()`

Any value taken from request data, request headers, URL path/query, filenames, DB rows that originated in user input (a stored push-subscription endpoint, a free-text title, an email/domain, etc.), or third-party API responses **must** pass through `scrub()` before reaching a `logger.X(...)` call. This prevents log injection (CWE-117): without it, `\r\n` in user input forges fake log lines and breaks SIEM parsers.

```python
from workspace.common.logging import scrub

logger.info("Autodiscover failed for domain %s", scrub(domain))
logger.warning("Push failed for %s: %s", scrub(sub.endpoint[:60]), e)
logger.exception("Activity provider '%s' failed", scrub(source))
```

**Rules:**

- Sanitize at the logger call site even when the value looks "safe" (a validated UUID, an enum slug, an email that passed `EmailField`). Validation runs at the view boundary, but the same value flows through Celery tasks, signals, and services to loggers far from where it was checked. CodeQL traces taint, not validation - the `py/log-injection` alert fires regardless.
- Never log full request bodies or headers. If you must, scrub them.
- Internal/system values that never touched user input (settings keys, hard-coded enum members, `__name__`, computed counts) don't need `scrub()`. Apply it to the *tainted* fields, not the whole format string.
- The helper lives in `workspace/common/logging.py`. The `str(...).replace('\r','').replace('\n','')` chain inside is the exact form CodeQL recognizes as a sanitizer for `py/log-injection` - do not refactor the replaces away or wrap them in another helper.

**Secrets are a separate concern.** `scrub()` stops log injection; it hides nothing. Fields whose
*name* marks them secret - `password`, `secret_key`, `session_key`, and anything prefixed `wrapped_`,
`encrypted_` or `sig_` - are redacted by `workspace/common/redaction.py`, on the console log handler
and on `DEFAULT_EXCEPTION_REPORTER_FILTER`. Extend that catalogue rather than remembering not to log
a field. It does not reach access logs (`django.server` and gunicorn own theirs), so never put a
secret in a URL.

Name matching cannot reach a frame's locals either - the local holding a wrapped key is called
`data`, not `wrapped_kex_priv` - so **a view handling secrets must declare it**. On a JSON API that
means `@sensitive_variables()`: `@sensitive_post_parameters` cleanses `request.POST`, which a JSON
body leaves empty. It is still worth keeping, because the default parser list also accepts a
form-encoded body, and on that request it is the only thing standing between the field and the
technical 500 page. `workspace/vault/views.py` is the worked example.

### Query parameter parsing - never trust raw values from `request.query_params` or `request.data`

Two recurring bugs land here, both because Python's loose typing or Django's deep-cleaning layer surface as confusing 500s instead of clean 4xxs:

**UUID parameters - validate at the boundary.** Passing a raw string straight to `Model.objects.get(uuid=...)`, `filter(uuid=...)`, or `Q(...uuid=...)` lets `UUIDField.to_python` raise `ValidationError` deep inside Django's cleaning layer. The surrounding `except Model.DoesNotExist` does **not** catch it - the exception escapes the view as a 500. Use `workspace.common.uuids.parse_uuid_or_none` instead:

```python
from workspace.common.uuids import parse_uuid_or_none

account_id = request.query_params.get('account')
if account_id:
    account_uuid = parse_uuid_or_none(account_id)
    if account_uuid is None:
        return Response(status=status.HTTP_404_NOT_FOUND)  # or 400 for collection filters
    try:
        account = MailAccount.objects.get(uuid=account_uuid, owner=request.user)
    except MailAccount.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)
```

When the UUID identifies a single resource (folder, account, label by id), map malformed input to **404** - the resource doesn't exist either way and 404 avoids leaking "format invalid" vs "not found". When it's a collection filter (e.g. `?account_id=` on a search endpoint), prefer **400** so the client sees the bug. URL kwargs declared with the `<uuid:>` path converter (e.g. `path('.../<uuid:uuid>', ...)`) are validated by Django at routing time, so they don't need this helper.

**Boolean parameters - never use Python truthiness on a string.** `if request.query_params.get('unread'):` is wrong: a non-empty string like `'false'` or `'0'` is truthy in Python, so a URL like `?unread=false` *enables* the filter the user is trying to disable. Use `workspace.common.booleans.is_truthy`, which mirrors DRF's `BooleanField.TRUE_VALUES`:

```python
from workspace.common.booleans import is_truthy

if is_truthy(request.query_params.get('unread')):
    qs = qs.filter(is_read=False)
```

Accepted true values: `true`, `1`, `yes`, `on`, `t`, `y` (case-insensitive). Everything else - including unknown strings, empty, `None`, and the false values - yields `False`. Permissive on purpose: a malformed boolean shouldn't 400 a search endpoint.

**Rules:**

- Every `objects.get(uuid=<request_value>)` or `filter(uuid=<request_value>)` in a view, AI tool args, or SSE handler must validate via `parse_uuid_or_none` first - unless the value is already typed (DRF serializer `UUIDField`, Pydantic `UUID`, URL `<uuid:>` converter).
- Every `if request.query_params.get('<flag>'):` that gates a filter or feature must use `is_truthy(...)` instead.
- For Pydantic-backed AI tool args, type the field as `uuid.UUID` rather than `str` so Pydantic rejects garbage at the tool-call boundary with a diagnostic error.

### Copying file content between rows - never assign a `FieldFile` directly

When duplicating an existing file/attachment into a new row (chat `save-to-files`, mail `save-to-files`, files `copy_node`, anything similar), **never** assign the source `FieldFile` straight to the destination model's `FileField`. Doing so makes both rows point at the same blob in storage, so deleting the source later silently breaks the destination.

The mechanism: Django's `FileField.pre_save` only invokes `storage.save()` (which generates a fresh storage path) when the value's `_committed` attribute is `False`. `FieldFile` (the descriptor returned for an existing row's `FileField`) carries `_committed=True`, so assigning it as-is is treated as "already in storage, do nothing" - the destination row gets the source's path verbatim.

```python
# ❌ Both rows now share the same blob. Delete the source -> destination orphans.
new_file.content = source.content                         # FieldFile, committed
new_file.save()
```

The fix is to wrap in `django.core.files.File` (or any other non-`FieldFile` `File` subclass: `ContentFile`, `UploadedFile`, ...). Those default to `_committed=False`, so `storage.save()` runs and streams the source via `content.chunks()` (default 64KB blocks) into a fresh path. This handles streaming AND the copy-correctness invariant in one move.

```python
# ✅ Streamed copy into a fresh storage path.
from django.core.files.base import File as DjangoFile

with source.content.open('rb') as f:
    new_file.content = DjangoFile(f, name=source.name)
    new_file.save()
```

**Rules:**

- Always pin this with a regression test that asserts `dest.content.name != source.content.name` after the copy AND that the bytes round-trip. Don't rely on a "content equality" check alone - the buggy version with a shared blob also passes a content check (it's the same blob).
- Wrap the open + save in `try/except (FileNotFoundError, OSError)` whenever copying user-uploaded content. A vanished blob otherwise surfaces as a bare 500 with no breadcrumbs. Mirror the response code of the closest read endpoint (404 for chat / mail attachment paths) and log the path through `scrub()` before re-raising or returning.
- `ContentFile(source.read(), ...)` happens to be _committed=False so it copies correctly, but it buffers the entire file in memory before re-emitting it. For anything that could grow (>1MB), prefer the `DjangoFile(open_stream, ...)` idiom.
- Existing precedent in the codebase: `workspace/files/webdav/resources.py:_copy_as` (already correct), `workspace/chat/views/attachments.py:AttachmentSaveToFilesView`, `workspace/mail/views/attachments.py:MailAttachmentSaveToFilesView`, `workspace/files/services/_storage_ops.py:copy_node`.

### Prefer the standard library over hand-rolled collection plumbing

We target Python 3.14, so reach for a stdlib primitive before writing manual loops over lists/sets: `itertools.batched` (chunking), `itertools.pairwise` (adjacent pairs), `itertools.chain.from_iterable` (flattening), `collections.Counter` (counting), `collections.defaultdict` (grouping), `dict.fromkeys` (order-preserving dedup), plus `math.prod` / `itertools.accumulate` / `statistics`.

Favour clarity, not cleverness: leave a loop explicit when it carries per-iteration side effects, and mind the [Refactoring & Optimization](#refactoring--optimization) rule (a test must cover the code first; the swap must preserve behavior). Two `batched` gotchas: it yields **tuples** (not slices), and ruff (`B911`) requires an explicit `strict=` - use `strict=False` for the usual short-final-batch case.

### Multi-type except clauses - unparenthesized is the house style (PEP 758)

We target Python 3.14, where PEP 758 makes `except ValueError, TypeError:` valid without parentheses (as long as there is no `as` capture). The codebase uses that form everywhere:

```python
try:
    return uuid.UUID(str(value))
except ValueError, TypeError:          # ✅ house style (no `as`)
    return None

except (ValueError, TypeError) as exc: # ✅ parentheses still MANDATORY with `as`
    ...
```

**Never rewrite these into `except (ValueError, TypeError):`.** If the code raises `SyntaxError` at import, you are running Python <= 3.13 (an old venv, a sandbox): that is an environment problem, fix the interpreter, not the code. A blanket parenthesization "fix" touches 40+ files of pure noise and has already been reverted once.

## Frontend Conventions

### `const`/`let`, never `var` - and camelCase for every global a script publishes

Production JS files are classic scripts, not ES modules, but that is a loading concern - it says nothing about how a binding is declared. Use `const` by default and `let` for what is genuinely reassigned; `var` is not the house style anywhere in `workspace/*/ui/static/`.

The one thing `var` buys is function-scope hoisting, and code that leans on it is code to rewrite rather than preserve: a `var` declared inside a `try` and read from the matching `catch` works by accident. **Declare it above the `try` as `let`** - the catch then reads a binding someone can see, and `undefined` means "that attempt never got that far" on purpose instead of by hoisting.

Globals a script publishes are camelCase, matching the rest of the codebase (`window.vaultApp`, `window.folderNav`, `window.pollUtils`) - whether the global is an Alpine component factory or a plain helper namespace. SCREAMING_SNAKE_CASE is for constants (`window.TAG_CHIP_COLORS`), and PascalCase is reserved for what is called with `new` or reads as a constructor (`VaultApiError`).

**The node:vm test caveat:** top-level `const`/`let` are not reachable as properties of the context the loader returns (see *JS unit tests* above), so a test asserting on a module-level constant needs it published on `window`. Two scripts loaded into the same context still see each other's top-level `const` - they share one global lexical scope - so cross-file constants do not need `var`.

### Drag & drop - the source must declare an `effectAllowed` containing the target's `dropEffect`

`preventDefault()` on `dragover` is **not** enough to accept a drop. The browser also checks that the `dropEffect` the target asks for is a member of the `effectAllowed` the source declared; if it isn't, it resets `dropEffect` to `none` and refuses the drop. No `drop` event fires, no request goes out, no error is raised - the zone still highlights, so it looks like the handler ran and did nothing.

Leaving `effectAllowed` unset does **not** mean "anything goes": the browser derives a default that varies by platform and drag source. Chrome on Linux derives `copyMove`, which excludes `link` - and clamps an explicit `effectAllowed = 'link'` down to `copy`.

```js
// ❌ WRONG - source says nothing, target asks for 'link'. Silently refused on Chrome/Linux.
onDragStart(e) { e.dataTransfer.setData('application/x-thing', id); }
onDragOver(e)  { e.dataTransfer.dropEffect = 'link'; }

// ✅ Correct - both sides name the same effect.
onDragStart(e) { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('application/x-thing', id); }
onDragOver(e)  { e.dataTransfer.dropEffect = 'copy'; }
```

**Rules:**
- Every `dragstart` handler sets `effectAllowed` explicitly. Every matching `dragover` sets a `dropEffect` that is a member of it. Grep both sides together: `grep -rn "dropEffect\|effectAllowed" --include=*.js --include=*.html workspace/`.
- Prefer `copy` for "add a reference to this" gestures and `move` for reordering. **Avoid `link`** - it is the one effect missing from `copyMove`, the most common derived default, and Chrome refuses it outright on Linux.
- Playwright cannot catch this. Its synthesized drags report `effectAllowed = 'all'`, which contains every effect, so the drop succeeds under test even when it is refused in a real browser. Assert the *negotiated values* on both sides (`effectAllowed` at `dragstart`, `dropEffect` at `dragover`), never just "did the drop land" - see `files/tests/e2e/test_pinned_drag_and_drop.py`.
- To diagnose a drop that never fires, log `effectAllowed`, `dropEffect` and `defaultPrevented` from a `dragover` listener in the real browser. `prevented=true` with `dropEffect=none` is this bug.

### Django template comments - `{# #}` is single-line ONLY

Django's lexer matches `{#.*?#}` **without** the DOTALL flag, so a `{# #}` whose closing delimiter is on another line is not a comment at all. The template engine treats the whole block as literal text and **renders your comment into the page**, visible to the user and shipped in the HTML.

```django
{# ✅ single line, opener and closer on the SAME line #}

{# ❌ WRONG - renders verbatim into the page
   this second line means the block was never a comment #}

{% comment %}
  ✅ Correct for anything spanning more than one line.
{% endcomment %}
```

There is no error, no warning, and no failing test - templates have no syntax check for this. The rendered page just grows a paragraph of prose in the middle of a table cell, and it survives review because the diff looks like a comment.

**Rules:**
- One line → `{# ... #}`. Two or more → `{% comment %}` / `{% endcomment %}`. No exceptions, no judgement call.
- Never wrap the same note in both.
- Don't use reStructuredText markup (``` ``double backticks`` ```) inside a template comment - that convention belongs to Python docstrings. Write plain prose.
- After touching a template comment, grep the rendered output rather than trusting the diff: `curl -s <url> | grep "<a few words of the comment>"` must return nothing.

### Alpine `init()` is auto-called - never add `x-init="init()"` on top of it

If your `x-data` component defines an `init()` method (`x-data="myApp()"` where `myApp` returns an object with `init() { ... }`), Alpine **automatically** invokes it when the element mounts. Adding `x-init="init()"` next to `x-data="myApp()"` runs `init()` a **second time**, silently:

```html
<!-- ❌ WRONG - init() runs twice -->
<div x-data="chatApp()" x-init="init()"></div>

<!-- ✅ Correct - Alpine auto-calls init() once -->
<div x-data="chatApp()"></div>
```

The bug is invisible: the second pass overwrites the first with the same data, no console warning, no broken UI. The visible cost is **double API calls and double event-listener registration** for everything in `init()`. We hit this in 4 modules (chat, mail, notes, dashboard) and the only diagnostic was a network-level audit.

**Rules:**
- Component objects with an `init()` method must rely on Alpine's auto-call. Do **not** also write `x-init="init()"`.
- `destroy()` is the matching teardown hook: Alpine auto-invokes it when the element leaves the DOM (alpine-ajax view swaps included). Only define it for cleanup - never name an action `destroy()`. We shipped a settings page whose "delete the project" confirm dialog popped up on every navigation away because the delete action was named `destroy()`.
- `x-init` is only for **inline expressions** on components that don't define an `init()` method (e.g., `<div x-data="{ open: false }" x-init="$watch('open', ...)">`).
- When adding event listeners inside `init()`, remember they will be added once per mount - if you ever do see two listeners firing, suspect a duplicate `x-init` or a duplicate `x-data` instantiation of the same component (see `filePreferences()` in `files/ui/index.html`, instantiated twice intentionally - its `init()` should be guarded against re-fetching).

### Never put a `get` accessor in a spread mixin - use a method

Components composed from mixins (`chatApp()`, `mailApp()`, `fileBrowser()`…) build themselves with object spread:

```js
return {
  ...chatBotMixin(),
  ...chatPanelsMixin(),
  ...
};
```

Object spread copies **values**, not property descriptors. A `get foo()` inside a mixin is therefore *called once* at spread time and the result is baked in as a plain data property. It never recomputes, Alpine has nothing to track, and the UI renders whatever the state happened to be before any fetch resolved - usually an empty array.

```js
// ❌ WRONG - inside a mixin that gets spread. Frozen to [] forever.
window.chatBotMixin = function chatBotMixin() {
  return {
    botMemories: [],
    get filteredBotMemories() { return this.botMemories.filter(...); },
  };
};

// ✅ Correct - a method is copied as a function and re-evaluated on every read.
window.chatBotMixin = function chatBotMixin() {
  return {
    botMemories: [],
    filteredBotMemories() { return this.botMemories.filter(...); },
  };
};
```

```html
<template x-for="mem in filteredBotMemories()" :key="mem.id">
```

The failure mode is silent and easy to misread: no console error, and sibling bindings that read the raw state (`x-text="botMemories.length"`) keep working, so the badge shows "3" next to an empty list. We shipped this twice - the AI Memory panel in chat, and the hidden-folders picker in mail.

**Rules:**
- A mixin (any function whose result is spread into a component) must never expose a `get` accessor. Write a method and call it with `()` from the template.
- A getter is fine **only** on the component's own root object literal - the one that isn't spread into anything. `filteredHiddenFolders` in `mail/ui/static/mail/ui/js/mail.js` is declared there for exactly this reason; keep the comment that says why.
- Reviewing a mixin: `grep -n "get [a-zA-Z_]*()" workspace/*/ui/static/*/ui/js/*.js` and check each hit is on a root literal.
- Getters on Alpine **stores** (`Alpine.store(...)`) are safe - stores are registered as objects, not spread.

### `$root` is a DOM element, not the parent component's data

`$root` resolves to `closestRoot(el)`: the nearest ancestor carrying `x-data`, **including the element's own component root**. Reading a data property off it therefore yields `undefined` whenever the expression sits inside a nested `x-data`, because you are asking a DOM node for a property it doesn't have.

```html
<!-- ❌ WRONG - $root is the inner chatAudioPlayer div, which has no recordedUrl -->
<div x-data="chatRecorderMixin()">
  <div x-data="chatAudioPlayer('preview', recordedDuration)">
    <audio :src="$root.recordedUrl"></audio>
  </div>
</div>

<!-- ✅ Correct - nested scopes inherit prototypally, so the bare name resolves -->
<audio :src="recordedUrl"></audio>
```

Alpine binds `:attr="undefined"` by **removing the attribute**, so the failure is completely silent: no console error, no broken layout, just an element quietly missing its `src`. We shipped this in the chat voice-message preview, where it produced a player with no audio source.

**Rules:**
- To read a property from an enclosing component, use the bare name. Nested `x-data` scopes inherit through the prototype chain; no qualifier is needed or correct.
- `$root` is for DOM work only - `this.$root.close()`, `this.$root.querySelector(...)`. Every legitimate use in this codebase is of that shape.
- When an `:attr` binding produces nothing at runtime, suspect an `undefined` expression before suspecting the attribute.

### `x-show` hides, `x-if` instantiates - it matters for `x-data` arguments

A component's `x-data` expression is evaluated **once**, when the element is first bound. `x-show` only toggles CSS, so a block that starts hidden is still constructed at page load, with whatever the surrounding state happened to be at that instant - usually empty.

```html
<!-- ❌ WRONG - evaluated at mount, when recordedDuration is still 0, and never again -->
<div x-show="recorderState === 'preview'" x-data="chatAudioPlayer('preview', recordedDuration)">

<!-- ✅ Correct - the component is constructed fresh each time the condition turns true -->
<template x-if="recorderState === 'preview'">
  <div x-data="chatAudioPlayer('preview', recordedDuration)">
```

The symptom is a component permanently stuck on its initial values while sibling bindings that read the state directly stay correct - the same misleading shape as the spread-getter bug above. In the voice-message preview it produced `0:03 / 0:00`, a progress bar frozen at zero and a dead seek bar.

**Rules:**
- If an `x-data` expression takes arguments that are not known at page load, gate the element with `x-if`, not `x-show`.
- `x-show` stays correct for a component whose constructor arguments are static, and it is cheaper - it does not tear down and rebuild the subtree.
- Never put both on the same element. `x-if` already controls presence; a leftover `x-show` is dead weight that hides the intent.
- `x-if` teardown runs the component's `destroy()`, so anything registered in `init()` must be released there or it accumulates across cycles.

### Embedding view data into JS - use `|json_script`, never `orjson.dumps + |safe`

When a Django view needs to hand off data to client-side JS (initial state, server-rendered preferences, serialized querysets that would otherwise force a redundant API call), **pass the raw Python object in context** and render it with Django's built-in `|json_script` filter:

```python
# View - pass the raw dict/list (NOT a JSON string)
return render(request, 'mail/ui/index.html', {
    'accounts': MailAccountSerializer(accounts, many=True).data,
    'oauth_providers': get_available_providers(),
})
```

```django
{# Template - |json_script renders <script id="..." type="application/json">...</script> #}
{{ accounts|json_script:"accounts-data" }}
{{ oauth_providers|json_script:"oauth-providers-data" }}
```

```js
// JS - read from the DOM
const accounts = JSON.parse(document.getElementById('accounts-data').textContent);
const providers = JSON.parse(document.getElementById('oauth-providers-data').textContent);
```

**Never** do this:

```python
# ❌ Manual dump in the view
'accounts_json': orjson.dumps(serializer.data).decode(),
```
```django
{# ❌ Inline raw JSON via |safe - XSS surface, no auto-escaping of </script> #}
<script id="accounts-data" type="application/json">{{ accounts_json|safe }}</script>
```

**Why `|json_script` is mandatory here:**
- It escapes `<`, `>`, `&`, `'`, `\u2028`, `\u2029` as JS-safe Unicode escapes - `</script>` injection is impossible even if the data contains user-controlled strings. Manual `|safe` defeats Django's auto-escape entirely; you'd have to remember to do `.replace('</', '<\\/')` everywhere (and inevitably forget once).
- It produces a `<script type="application/json">` block, which the browser parses as data, not code - no eval, no parser tricks.
- It's built into Django (since 2.1) - no extra import, no `orjson`/`json` boilerplate in the view.

**Naming convention - drop `_json` from context variable names:** the value passed to the template is now a Python dict/list, not a JSON string. Naming it `accounts_json` is a lie. Always name the context variable for what it *is*:

| ❌ Old name | ✅ New name | Type at the view boundary |
|---|---|---|
| `accounts_json` | `accounts` | dict / list |
| `prefs_json` | `prefs` | dict |
| `calendars_json` | `calendars` | dict |
| `folders_json` | `folders` | list |

The script tag's `id` attribute is the right place for the `*-data` suffix (e.g., `id="accounts-data"`), not the Python context key.

**Exception** - when a context variable name collides with another already in context (e.g., a view passes both a queryset of accounts and the serialized version), check whether the queryset version is actually used in the template. It is often **dead context** (the template only reads `accounts` from the JS side via the embedded script tag). If so, delete the dead key; don't keep both.

### Server-rendered partial swaps - use alpine-ajax, never raw `fetch`

Whenever a piece of UI needs to be refreshed from a Django partial (lists, feeds, sidebars, popovers, folder trees, anything rendered server-side), **use [alpine-ajax](https://alpine-ajax.js.org)**. The library is already loaded globally in `base.html`.

**Never** write a hand-rolled `fetch(...).then(r.text()).then(html => el.innerHTML = html)`, `DOMParser` parsing of an HTML response, or `target.replaceWith(newNode)` + `Alpine.initTree()` pipeline. That pattern silently destroys every Alpine binding inside the swapped subtree (context menus, drag-and-drop handlers, `x-show` state, `x-model` bindings) because raw `innerHTML` assignment doesn't morph - it rebuilds the tree from scratch.

#### How to trigger a swap

**Declarative (user-triggered):** put `x-target="<target-id>"` on the `<a>` / `<form>` / `<button>` the user interacts with. The response must contain an element with matching `id`.

```html
<a href="{% url 'chat_ui:conversation_list' %}" x-target="conversation-list">Refresh</a>

<div id="conversation-list">
  {% include "chat/ui/partials/conversation_list.html" %}
</div>
```

**Programmatic (from an Alpine expression or a component method):** use the `$ajax(url, options)` magic. This is the **only** supported way to initiate a request from JavaScript - do not fake a user click on a hidden link.

```html
<!-- Inline Alpine expression -->
<input @input.debounce.300ms="$ajax('/chat/conversations?q=' + encodeURIComponent(query), { target: 'conversation-list' })">
```

```js
// Inside a component method (x-data): `this.$ajax` is available just like `this.$refs`.
refreshList() {
  this.$ajax('/chat/conversations', { target: 'conversation-list' });
}
```

Available `$ajax` options: `method` (default `'GET'`), `target` (id of the element to swap, **without** a `#`), `targets` (array, overrides `target`), `body`, `headers`, `focus`, `sync`.

#### Lifecycle events

`ajax:before`, `ajax:send`, `ajax:success`, `ajax:error`, `ajax:after` bubble up the DOM. Listen on the component root with `@ajax:error="showAlert('error', 'Failed')"` instead of wrapping the call in a `try/catch`.

#### Server side

Return the partial template directly - no JSON envelope, no wrapping. The endpoint often checks `request.headers.get('X-Alpine-Request')` so the same URL can serve the full page (browser refresh) and the fragment (alpine-ajax swap). Existing examples: `workspace/chat/ui/views.py:conversation_list_view`, `workspace/users/ui/views.py:profile_activity_feed`, `workspace/files/ui/views.py` (the `#folder-browser` branch).

### UI Partials

Always use the existing UI partials located in `workspace/common/templates/ui/partials/` instead of writing inline HTML for common components.

#### Alerts

Use the `<inline-alert>` custom element for all inline alert messages (defined in `workspace/common/static/ui/js/inline_alert.js`, loaded globally by `base.html`). One implementation for both rendering paths: Django templates write the element directly, runtime JS creates it with `document.createElement('inline-alert')` and the same attributes.

```django
<inline-alert type="error" message="Your error message"></inline-alert>
<inline-alert type="warning" message="Heads up" class="mb-4"></inline-alert>
<inline-alert type="success" title="Saved" message="Your changes are in." dismissible></inline-alert>
```

Available attributes:
- `type`: 'info' (default), 'success', 'warning', 'error'
- `message`: plain-text body
- `title`: optional bold heading above the message
- `dismissible`: boolean attribute - adds a close button that removes the alert
- `icon`: lucide icon name override; `icon="none"` hides the icon
- `class`: additional CSS classes (e.g., "mb-4"), merged with the element's own

Dynamic or rich content goes in as child content instead of `message` (slot mode); action buttons are children with `slot="actions"` (style them yourself, `data-dismiss` also removes the alert):

```django
<inline-alert type="error"><span x-text="error"></span></inline-alert>
<inline-alert message="A new version is available.">
  <button slot="actions" class="btn btn-xs btn-primary" @click="reload()">Reload</button>
  <button slot="actions" class="btn btn-xs btn-ghost" data-dismiss>Ignore</button>
</inline-alert>
```

Attributes and children are read once, when the element first connects - author them before inserting it, and use slot mode (not attribute bindings) for text that changes afterwards.

#### Dialogs

Use the `dialogs` partial for modal dialogs instead of inline modal HTML.

#### Other Available Partials

- `app_logo.html` - Application logo
- `breadcrumbs.html` - Breadcrumb navigation
- `comments.html` - comment thread (list + collapsed-until-focused composer + inline edit) backed by `commentsComponent()` from `common/static/ui/js/comments.js`. Params: `list_url` (collection endpoint; item endpoints are `<list_url>/<uuid>`), `current_user_id`, `can_comment`. Used by the files properties panel and the task panel - reuse it for any new commentable entity instead of copying the markup.
- `navbar.html` - Navigation bar
- `refresh_button.html` - Alpine-AJAX refresh button (spins while `loading` is truthy). Params: `url_expr`, `target`, optional `loading_expr` / `title` / `size`.
- `user_avatar.html` - User avatar display
- `user_chip.html` - removable avatar+name chip for "selected users" lists (guests, invitees, assignees, member pickers). Params are Alpine expression fragments: `user_id_expr`, `username_expr`, optional `remove_expr` (omit for read-only) and `remove_show_expr`. Never hand-roll this chip again - every hand-rolled copy has ended up with an off-center avatar.
- `user_selector.html` / `group_selector.html` - search-as-you-type pickers dispatching a custom event on select (see the comment block at the top of each file for params)

### File Actions

Any frontend element that triggers an action on a file or folder (rename, delete, favorite, share, move, pin, download, etc.) **must** check availability against `POST /api/v1/files/actions` before letting the user click. Never hard-code availability rules in the frontend (no "is journal note?", no "is owner?", no "is shared with me?" checks duplicated client-side). The backend `ActionRegistry` (`workspace/files/actions/`) is the single source of truth, and `RenameAction.is_available()` / `DeleteAction.is_available()` / etc. already encode all the rules.

**Rules:**

- Context menus: fetch the action list for the target file(s) via `/api/v1/files/actions` and render only the returned actions - never render a static list of menu items.
- Buttons, links, inline inputs (e.g., title input for rename): bind their `:disabled` / `:readonly` attribute to the presence of the corresponding action ID in the fetched list. Default to disabled while the list is loading (fail-safe).
- When implementing a new file-manipulating UI element, first check that an `is_available` entry exists for the action in `workspace/files/actions/`. If not, add it - don't ship the UI without it.
- Defence-in-depth: the JS handler that performs the action (e.g., `renameNote`, `deleteNote`) must also early-return if the action isn't in the cached list. Prevents stale state from producing a request the backend will 403 anyway.

**Endpoint contract:**

```js
const resp = await fetch('/api/v1/files/actions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
  body: JSON.stringify({ uuids: [fileUuid] }),
});
const data = await resp.json();
// data[fileUuid] is an array of { id, label, icon, category, shortcut, css_class, bulk }
const actionIds = (data[fileUuid] || []).map(a => a.id);
```

Use a race-protection counter (see `_loadGeneration` in `workspace/notes/ui/static/notes/ui/js/notes.js:557`) when the fetched list feeds reactive state that depends on the current selection - rapid selection changes otherwise lead to stale results being applied.

**Scope:** applies to the `files` module and every module whose UI manipulates files (notes, mail attachments, chat attachments, etc.). If a module manipulates another kind of entity with its own action registry (not files), follow the same principle against that module's equivalent endpoint.
