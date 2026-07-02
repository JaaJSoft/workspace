# Notifications

In-app and Web Push notifications with priority levels and read tracking.

## Features

- **In-app notifications** - A notification center with an unread badge, delivered live so the badge updates without a page reload.
- **Web Push** - Browser push notifications via the Web Push protocol (VAPID), so users are reached even when the app is closed. Users subscribe per device.
- **Priority levels** - `low`, `normal`, `high`, and `urgent`, so important notifications stand out.
- **Rich metadata** - Each notification carries an icon, color, title, body, optional deep-link URL, and an optional actor (the user who triggered it).
- **Read tracking** - Per-notification read state with a mark-all-as-read action; the unread badge is backed by a partial index for fast counts.
- **Cross-module origins** - Any module can raise a notification (a new chat message, a file share, a calendar invite, ...) tagged with its origin for filtering.

## Web Push setup

Web Push is optional and disabled until VAPID keys are configured. Generate a key pair and set:

| Variable | Purpose |
|---|---|
| `WEBPUSH_VAPID_PRIVATE_KEY` | VAPID private key |
| `WEBPUSH_VAPID_PUBLIC_KEY` | VAPID public key (sent to the browser) |
| `WEBPUSH_VAPID_MAILTO` | Contact `mailto:` address required by the push protocol |

See [`.env.example`](../../.env.example) for the generation snippet. Push delivery runs as background work, so **Celery worker should be running in production** for reliable delivery.

## API

All endpoints under `/api/v1/notifications/` - see the [Swagger UI](/schema/swagger-ui/) for full documentation.
