# Files

Upload, organize, and preview files with a full-featured file explorer.

![Grid view](../images/files_1.png)

![List view](../images/files_2.png)

## Features

- **Folder hierarchy** - Nested folders with breadcrumb navigation and path tracking
- **Multiple views** - Grid (mosaic) and list views with customizable columns
- **Drag & drop** - Upload files and move items between folders
- **Built-in viewers** - Preview PDF, Markdown, images, video, audio, and code files inline
- **Office documents** - View and edit DOCX/XLSX/PPTX in the browser through a self-hosted WOPI editor (Collabora, OnlyOffice)
- **Favorites & Recent** - Star files for quick access, track recently opened items
- **Trash** - Soft delete with configurable retention period before permanent removal
- **Thumbnails** - Auto-generated thumbnails for image files
- **Search** - Full-text search over file names and, for text formats
  (Markdown, plain text, CSV, HTML, JSON, XML, source code), file contents;
  filter by type and sort
- **Tags** - Create and assign tags to organize files across folders
- **Sharing** - Share files with specific users or via public links with optional password protection and expiration
- **Pinned folders** - Pin frequently used folders to the sidebar
- **Group folders** - Shared folder spaces for team collaboration
- **Folder download** - Download entire folders as ZIP archives
- **WebDAV** - Access files from any WebDAV-compatible client
- **File locking & comments** - Lock files to prevent concurrent edits, add comments

## Content search

Files are searchable by name and, for text formats, by what is written inside them:
Markdown, plain text, CSV, HTML, JSON, XML and source code. Anything else - PDFs, office
documents, images, archives - stays searchable by name.

The index holds only the extracted words, never a copy of the file: the blob on disk
remains the single copy of the content. Because nothing in the database can rebuild it,
an existing installation needs one backfill after upgrading:

```bash
python manage.py reindex_files_search
```

A fresh installation needs nothing - files are indexed as they are created. Run the same
command again only after a change to how text is extracted, so already-indexed files pick
up the new extraction.

## Office Documents

Word, Excel and PowerPoint files (plus their OpenDocument equivalents: ODT, ODS, ODP) open directly in the file viewer when a WOPI editor is deployed next to the app — and users with write permission can edit and save them from the browser. Coverage follows the editor: every document-family format its discovery advertises becomes viewable (RTF, legacy binary suites, templates like DOTX, XLSB, ...), and formats the editor can only render open read-only. Without an editor, office files are download-only.

The integration speaks the WOPI protocol, so the editor is the deployer's choice:

| Editor | Notes |
|---|---|
| [Collabora CODE](https://www.collaboraonline.com/code/) | Free, single container, the setup shipped in the deployment examples |
| Collabora Online | Same protocol, commercial support |
| [OnlyOffice Docs](https://www.onlyoffice.com/) | Community or Enterprise, in WOPI mode (>= 6.4) |
| Office Online Server | Microsoft's on-premises WOPI client, for deployers licensing it |

How it behaves:

- **Permissions are live** - view-only shares open read-only, and revoking someone's access cuts their editing session at the next editor round-trip.
- **Saves are ordinary writes** - a save from the editor goes through the same pipeline as an upload: content hash, thumbnail regeneration, file events.
- **Locking is integrated** - an editing session locks the file for the rest of the app, and an in-app lock held by someone else blocks editing.
- **Graceful degradation** - editor unreachable or format unsupported → the viewer offers the download instead. Nothing else depends on the editor.

Setup lives in the deployment examples: [Docker Compose](../deployments/docker-compose/README.md#office-documents-optional) and [Kubernetes](../deployments/kubernetes/README.md#office-documents-optional). The short version: run the editor container, point `WOPI_DISCOVERY_URL` at its `/hosting/discovery` endpoint, and give the browser a route to the editor (its iframe loads from the editor's own hostname).

## API

All endpoints under `/api/v1/files/` - see the [Swagger UI](/schema/swagger-ui/) for full documentation. The WOPI endpoints used by the editor live under `/api/wopi/files/` - their shape is fixed by the [WOPI protocol](https://learn.microsoft.com/en-us/microsoft-365/cloud-storage-partner-program/rest/) and they authenticate with per-session access tokens, not user sessions.

## Malware scanning

Uploaded file content can be scanned by a [ClamAV](https://www.clamav.net/)
daemon. The feature is **off by default**: a single-user instance does not need
it, and the Raspberry Pi deployment target cannot run the daemon.

Scanning runs after the upload response, from the same Celery pipeline that
builds thumbnails and the search index, so it never slows an upload down. Every
write path is covered - the REST API, WebDAV, the office editor's save, archive
extraction and imports all record the file event the scanner subscribes to.

### Enabling it

Point the application at a running `clamd`:

```bash
FILES_MALWARE_SCAN_ENABLED=1
# Unix socket (takes precedence when set):
FILES_CLAMAV_SOCKET=unix:///var/run/clamav/clamd.ctl
# or TCP:
FILES_CLAMAV_HOST=127.0.0.1
FILES_CLAMAV_PORT=3310
```

### Policy

| Setting | Values | Effect |
|---|---|---|
| `FILES_MALWARE_ON_DETECTION` | `block` (default), `flag` | `block` quarantines an infected file: it cannot be downloaded, previewed or found in search, cannot be attached to a message, an email or a task, is not readable by the AI assistant, and its owner sees it marked as quarantined. `flag` records the verdict and leaves the file usable. |
| `FILES_MALWARE_ON_ERROR` | `open` (default), `closed` | What happens to a file the scanner could not examine - a daemon that is down, a blob that vanished. `open` leaves it usable, `closed` quarantines it. |
| `FILES_MALWARE_SCAN_MAX_BYTES` | bytes, default 100 MiB | Files larger than this are recorded as `skipped` and stay downloadable. |

A file whose scan has not run yet stays downloadable. The window is normally a
few seconds; a persistent backlog shows up as a stalled queue on the admin
dashboard.

**The daemon has its own cap.** `clamd`'s `StreamMaxLength` defaults to 25 MB,
so out of the box the daemon refuses before `FILES_MALWARE_SCAN_MAX_BYTES` does,
and the file is recorded as `skipped`. Raise `StreamMaxLength` in
`clamd.conf` if you want the application-side cap to be the effective one.

### Monitoring

The admin dashboard carries three cards: quarantined files, scanner errors in
the last 24 hours, and live daemon reachability. The full verdict history is at
**Files > Malware scans** in the admin.

### Backfilling an existing library

```bash
python manage.py scan_files              # queue every file with no verdict
python manage.py scan_files --rescan     # re-scan everything
python manage.py scan_files --dry-run    # count without queueing
```

### Verifying a real engine

The test suite drives a fake daemon, so it never needs ClamAV and there is no
EICAR string in this repository. To check a real deployment, write the
[EICAR test file](https://www.eicar.org/download-anti-malware-testfile/) into a
scratch directory outside the checkout, upload it, and confirm it is
quarantined within a few seconds.
