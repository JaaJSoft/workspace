# Imports

Bring files over from another cloud - a Nextcloud account or any WebDAV folder - into the Files module. Preview module.

## Features

- **Connections** - A remote source (URL, username, app password) verified against the server before it is saved; secrets are stored encrypted
- **Wizard** - Pick a connection, what to import, the remote folder to start from, the local destination and what to do with same-name files (keep both, keep mine, replace)
- **Background jobs** - The import runs in the worker in time slices, so a multi-hour migration never meets the task time limits; progress is live on the Imports page and a notification arrives when it ends
- **Resumable and incremental** - A job picks up where it stopped after a pause or a restart; running it again on the same connection only fetches what changed (new files, new versions), and a retry skips what already went through
- **Error report** - Every entry that could not be imported is listed with its reason, and a retry only runs those and the ones never reached
- **Quota aware** - The remote tree is listed and sized first; the import is refused up front when it would not fit

## Providers

| Provider | Notes |
|---|---|
| Nextcloud | Paste the instance URL; the per-user WebDAV root is derived from it. Use an app password (Settings → Security). Server version and apps are read from OCS for information |
| WebDAV | Any server: give the full URL of the folder to import from |

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `IMPORTS_BATCH_SECONDS` | `1200` | A running job yields and re-enqueues itself after this long, so the Celery time limits are never hit |
| `IMPORTS_HTTP_TIMEOUT` | `60` | Per-request timeout (seconds) when talking to the remote |
| `IMPORTS_MAX_CONSECUTIVE_ERRORS` | `20` | Consecutive remote failures after which a job gives up instead of burning through a dead connection one entry at a time |
| `IMPORTS_JOB_RETENTION_DAYS` | `90` | Per-entry error reports of jobs finished longer ago than this are purged nightly. Successful entries are kept: they are what makes later runs incremental |
| `IMPORTS_ALLOW_PRIVATE_NETWORKS` | `False` | Allow remote URLs on private networks (10/8, 172.16/12, 192.168/16, fc00::/7, carrier-grade NAT) |
| `IMPORTS_ALLOWED_HOSTS` | empty | Hosts that skip the URL check entirely, comma-separated |

## Remote URL safety

The worker fetches whatever URL a connection carries, which makes an unchecked URL a server-side request forgery. Every URL is resolved and vetted when the connection is created, updated, tested or browsed, and again at the start of every job slice: loopback, link-local (cloud metadata), unspecified, multicast and reserved addresses are always refused, private networks unless `IMPORTS_ALLOW_PRIVATE_NETWORKS` is on. Redirects are never followed, and remote paths may not contain `.` or `..` segments.

Known limitation: the check and the HTTP client resolve the host separately, so a name that alternates between a public and a private answer (DNS rebinding) has a window between the check and the request. Re-checking every slice keeps it short; only pinning the resolved address at the transport level would close it.

## How a job runs

1. **Listing** - the remote tree under the chosen folder is walked once to count files and bytes (entries already imported with the same options count as unchanged and are not fetched) and the storage quota is checked.
2. **Copying** - folders are created (an existing same-name folder is reused), files are streamed into a spool and handed to the Files module; each entry is recorded as done, skipped or failed with its version marker (etag, else size and mtime).
3. The job stops between entries when cancelled or when its slice is over; the next slice resumes from the persisted state. A transfer cut short by the slice limit is retried once, then the entry is reported failed rather than downloaded again forever.
4. On completion, failure or cancellation the owner is notified with a one-line summary.
