---
name: reviewing-access-control
description: Use when adding or reviewing an endpoint, viewset action, alpine-ajax view, WebDAV handler, AI tool, SSE handler or Celery task that reads or changes data on behalf of a user, or when auditing a module for IDOR / broken access control (another user's rows readable, writable or attachable). Also use before merging a PR that adds a serializer field holding a foreign key or UUID, a new action in an ActionRegistry, or work that runs later with a user's identity.
---

# Reviewing Access Control

## Overview

Every entry point that takes an identifier from the outside must answer two questions about the row it names: *may this user reach it*, and *may this user do this to it*. The access helpers listed in CLAUDE.md ("Access Control Querysets") answer the first; the "Authorization rules" section right below them lists where the codebase has failed to ask either. This skill is the procedure: trace every identifier from the request to the ORM, check the right question is asked on every path, and prove each hole with a failing test before fixing it.

## When to Use

- A new or changed view, viewset action, WebDAV method, AI tool, SSE handler or task
- A serializer gaining a `PrimaryKeyRelatedField`, a `SlugRelatedField` or a UUID field
- A new action in `files/actions/`, the projects or vault registry
- A Celery task that acts with a user's identity (`acting_user`, `created_by`, `human_user`)
- Auditing a whole module (enumerate everything below, not only the diff)

## Procedure

### 1. Enumerate the entry points

For the module (or the diff): every route in `urls.py`, `ui/urls.py` and the DRF routers, every `@action`, every WebDAV resource method (`files/webdav/resources.py`), every AI tool (`<module>/ai_tools.py`, `ai/tools.py`), every SSE provider, every task enqueued from a request. A module audit that skips WebDAV or the AI tools misses the paths most likely to bypass the UI's checks.

### 2. Trace each identifier

For each entry point, list every value coming from outside: URL kwargs, query params, body fields, serializer fields, tool arguments, WebDAV `Destination` headers, cookies. For each one, follow it to the ORM fetch and answer:

| Question | Red flag |
|---|---|
| Is the row fetched through the module's access helper? | `Model.objects.get(uuid=...)`, `filter(pk__in=request_ids)` without the helper |
| Is a serializer FK narrowed? | `queryset=Model.objects.all()` (the `ModelSerializer` default) with no check in `validate()` |
| Is the check done on create **and** update? | `if "parent" in attrs and instance is not None:` - the create path skips it |
| Is a list of ids intersected with the visible set? | `Q(calendar_id__in=request_ids)` instead of `visible_calendar_ids(user) ∩ request_ids` |
| Is a malformed UUID a clean 4xx? | raw string into `filter(uuid=...)` → `ValidationError` → 500 (`parse_uuid_or_none`) |

### 3. Check the permission level, not just reachability

Reaching a row is not being allowed to change it. For each write:

- **Level**: files distinguish VIEW (share-ro), WRITE (share-rw, content only), EDIT (group member), MANAGE (owner). Creating children, renaming, moving, deleting, sharing and locking each need a specific level - a lock taken with VIEW blocks the owner.
- **Whose thing**: in a shared space (conversation, project, group folder) membership allows reading; changing another member's object (their goal, their message, everyone's history) needs an explicit rule - creator, admin or owner - in a service helper.
- **Ownership in a shared space**: an owner shortcut must still require current membership of the space; leaving it must revoke what the user handed out there.
- **State**: trashed (`deleted_at`), quarantined (`is_blocked`), external/synced (feed calendars), wrong node type (a file used as a folder) - each write must refuse the states it cannot apply to.

### 4. Check every path of the same action

List the paths that perform the action: REST, alpine-ajax UI, WebDAV, AI tool, bulk endpoint, copy/extract/import side doors. They must all ask the same question, which means the rule belongs in the service (or the registry's `is_available`, consulted by the service or the view before acting) - never only in the template or the menu. Diff the checks of the REST path against the WebDAV and AI paths: that is where divergence hides.

### 5. Check deferred work

For a task that runs later with someone's identity: at run time, is the user still active, still a member of the space, still allowed to use what the task uses (a bot, a mailbox, a folder)? If not, the task must stop - and deactivate the job - rather than run.

### 6. Check what leaves

Serializers and payloads must not carry another user's credential or private data: tokens (`voter_token`, share passwords, push keys), guest emails, private names of contacts the viewer lost access to. Mark the caller's own rows (`is_mine`) instead of shipping the secret that proves ownership.

### 7. Prove, then fix

For each finding:

1. Write the regression test first in the module's `tests/` (an `APITestCase` with two users is usually enough: the owner sets up, the other one attacks) and **watch it fail** against the current code.
2. Put the rule in the module's `services/` or `queries.py`; views, serializers, WebDAV and tools only call it.
3. Answer an unreachable row the way the module already does (404, or the same 400 a nonexistent UUID gets) so the response does not reveal it exists; return fixed messages, never `str(exception)`.
4. Run the module suite and the suites of the modules built on it (files changes reach notes, photos, chat, mail, projects, imports).

Never use `git stash` to switch between the fixed and unfixed code when several worktrees share the repository - the stash list is shared. Save the source change as a patch, `git checkout -- <paths>`, run the test, `git apply` it back.

## Reporting a finding

One line per finding is enough for the reviewer: `file:line`, entry point (method + URL, or tool name), the concrete scenario (who does what, what happens), severity (cross-user read/write of private data > cross-user attach or permission-level bypass > consistency only), and the helper the fix should use. Separate "confirmed by a failing test" from "by code trace only".

## Common false positives

- A writable FK whose queryset is unfiltered but which `validate_<field>` checks through the helper (`ProjectSerializer.groups`).
- A serializer only ever used for output (`MailRuleLogSerializer.message`).
- A public endpoint documented as such (avatar by UUID, a share-token route) - check what it exposes, not that it is public.
- "Invite anyone" fields (event members, poll invitees): granting access to the invitee is the feature; check it grants nothing beyond the invited object.
