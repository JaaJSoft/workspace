---
name: preparing-changelog
description: Use when preparing the CHANGELOG.md entry for the next release, writing release notes, or summarizing what changed since the last version tag for users. The changelog is shown in the in-app "What's new" modal, so it is written for end users, not admins or developers.
---

# Preparing the Changelog

## Overview

`CHANGELOG.md` is rendered in the in-app "What's new" modal. Its readers are **end users**: they don't run the server, they don't read the code, and they don't care how a fix was made. Each bullet says what they will notice. The format rules (heading, Highlights, one section per area, what to exclude, tone) are in the *Changelog* section of `CLAUDE.md`. This skill is the workflow on top of them.

## 1. Collect the range

```bash
last=$(git describe --tags --abbrev=0)          # e.g. v0.42.0
git merge-base --is-ancestor "$last" HEAD && echo ok   # the tag must be on the current branch
git log "$last"..HEAD --format='%h %s'
```

The new version is the next minor after `$last` unless the user says otherwise. Don't bump `pyproject.toml`: the `release:` commit does that.

## 2. Drop what never goes in

Drop these before you group anything:

- **Preview modules.** Any module registered with `preview=True` (`grep -l "preview=True" workspace/*/apps.py`). Users aren't told about it until it leaves preview. That means no section, no bullet, and no mention in Highlights or the title, even when most of the release went into it.
- `chore(deps)`, `ci`, `test`, `docs`, `refactor` and `style` commits with no visible effect. Read a `refactor` before dropping it: some change behaviour (a back button that now works).
- Admin and ops topics: logs, metrics, settings, migrations, worker counts, background housekeeping tasks.

## 3. Read the PRs, not the titles

A commit title is written for a developer ("close cross-user access gaps on group folders"). The PR's Summary says what changed for whoever uses the app. Read it before you write the bullet:

```bash
gh pr view <n> --json title,body -q '.title + "\n" + (.body | split("## Changes")[0])'
```

## 4. Group and write

- One `###` section per area the user recognizes (Files, Chat, Calendar, AI Assistants, WebDAV, People, Interface, Reliability, Fixes...). Order them by importance, with Fixes last.
- One bullet per user-visible change. Merge commits that change the same thing.
- Describe the outcome, not the mechanism. Leave out chunk sizes, endpoints, task names and library names. Numbers are fine when the user can feel them ("about twice as fast").
- **Behaviour changes are announced as such**, with the reason, when something disappears or stops working the way it used to (for example, bots removed from group conversations). A user who loses something must find out why here.
- Highlights: 1-2 sentences, no list. Title: 2-4 words on the release's theme.

## 5. Security fixes: stay vague

Access-control, XSS or leak fixes go in **one short `### Privacy & Security` section** made of 1-2 general bullets. Don't list each gap: the changelog is public, and a precise list of what used to leak tells someone exactly what to try on an instance that hasn't upgraded yet.

```markdown
<!-- ✅ -->
- Your workspace is more secure. Sharing, group folders, calendars and projects now respect permissions more strictly, so everyone sees and changes only what they are allowed to.

<!-- ❌ -->
- Members of a group can no longer move, rename or trash the group's main folder.
- Guests of a poll keep their votes to themselves.
```

The Highlights follow the same rule: "more secure, with stricter permissions", never "closes gaps that let people see what was not theirs".

## 6. Check before handing over

- No em-dash or en-dash anywhere in the entry (`grep -n "[—–]" CHANGELOG.md`).
- No preview module named, including in the Highlights.
- No implementation detail left: file names, endpoints, `snake_case` identifiers, library names.
- Don't commit unless asked. If a commit is wanted and the current branch is `main`, create a branch first.
