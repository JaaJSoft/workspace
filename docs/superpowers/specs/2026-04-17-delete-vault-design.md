# Design — Delete Vault API

**Date:** 2026-04-17
**Branch:** feat/passwords-module

## Overview

Add `DELETE /api/v1/passwords/vaults/<uuid>` to allow the vault owner to permanently delete a vault along with all its contents and shared access records.

## Endpoint

```
DELETE /api/v1/passwords/vaults/<uuid>
```

- Authentication required (`IsAuthenticated`)
- Permission: `request.user == vault.user` (owner only)
- Non-owners (including managers) receive `404` — same response as "not found" to avoid disclosing vault existence

## Service

Add `VaultService.delete_vault(user, vault_uuid) -> bool` in `workspace/passwords/services/vault.py`:

1. Fetch vault via `VaultService.get_vault(user, vault_uuid)` — returns `None` if not found or no access
2. Check `vault.user == user` — return `False` if not owner
3. Call `vault.delete()` — Django CASCADE handles all related objects
4. Return `True`

## Cascade (automatic via Django)

`vault.delete()` cascades to:
- `PasswordEntry` / `LoginEntry` (including soft-deleted / trashed entries)
- `PasswordFolder`, `PasswordTag`, `PasswordEntryTag`
- `VaultMember` (removes shared access for all members)
- `VaultGroupAccess`

No additional application-level cleanup required.

## View

Extend `VaultDetailView` with a `delete` method:

```python
def delete(self, request, uuid):
    vault = VaultService.get_vault(request.user, uuid)
    if vault is None or vault.user != request.user:
        return Response({'detail': 'Not found.'}, status=404)
    vault.delete()
    return Response(status=204)
```

## HTTP Responses

| Case | Code |
|------|------|
| Success | `204 No Content` |
| Not found or not owner | `404 Not Found` |
| Unauthenticated | `401 Unauthorized` |

## Tests

### Service tests — `workspace/passwords/tests/test_vault_service.py` (`DeleteVaultTests`)

| Test | Description |
|------|-------------|
| `test_owner_can_delete_vault` | Owner deletes own vault → returns True, vault gone |
| `test_non_owner_cannot_delete_vault` | Other user cannot delete → returns False, vault intact |
| `test_manager_member_cannot_delete_vault` | Manager role cannot delete → returns False, vault intact |
| `test_delete_cascades_entries` | Deleting vault removes all its entries |
| `test_delete_cascades_members` | Deleting vault removes all VaultMember rows |
| `test_delete_nonexistent_vault_returns_false` | Unknown UUID → returns False |

### View tests (manual / integration)

| Scenario | Expected |
|----------|----------|
| `DELETE /api/v1/passwords/vaults/<uuid>` as owner | `204 No Content` |
| `DELETE /api/v1/passwords/vaults/<uuid>` as member | `404 Not Found` |
| `DELETE /api/v1/passwords/vaults/<uuid>` unauthenticated | `401 Unauthorized` |
| `DELETE /api/v1/passwords/vaults/<uuid>` non-existent UUID | `404 Not Found` |
