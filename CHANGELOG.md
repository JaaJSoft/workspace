# Changelog

## 0.3.0

- Add folder download as ZIP archive via `/api/v1/files/<uuid>/download`
- Extend file download endpoint to support both files and folders
- Update context menu with download option for folders ("Download as ZIP")
- Fix release script consistency and license change date formatting

## 0.2.0

- Add PostgreSQL support via `dj-database-url` and `psycopg`
- Sync Monaco editor basic theme with DaisyUI theme
- Add GitHub Actions CI workflow for running tests
- Configure Dependabot for `uv` package ecosystem
- Add release automation script

## 0.1.0

Initial public release.

- File browser with navigation, breadcrumbs, keyboard shortcuts, drag & drop upload, favorites, trash, and bulk actions
- Monaco Editor for text/code files with full toolbar and persisted preferences
- Milkdown Crepe WYSIWYG for Markdown with slash commands and DaisyUI theme integration
- Image, PDF, and media viewers
- WebDAV integration with Django authentication
- Per-user settings system with theme selection
- Dashboard, responsive sidebar, unified search, and help modal
- Module registry with dynamic module management
