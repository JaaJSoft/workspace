# Connecting Gmail with OAuth2

Google stopped accepting a plain account password for IMAP and SMTP: personal Gmail accounts can only be reached with an app password (which requires 2-Step Verification and is disabled in many Google Workspace organizations) or with OAuth2. OAuth2 is the supported path, and the one this guide covers.

Registering the OAuth client is a **one-time, instance-wide job for the administrator**. Once `OAUTH_GOOGLE_CLIENT_ID` and `OAUTH_GOOGLE_CLIENT_SECRET` are set, a **Connect with Google** button appears in *Mail > Add account* for every user of the instance, and each of them connects their own mailbox in one click. No user ever sees the client secret.

The same procedure works for a Google Workspace mailbox; the differences are called out in [Choose the audience](#2-choose-the-audience).

## What happens under the hood

Useful to know before touching the Google console, because it explains what Google needs to be told:

1. The user clicks **Connect with Google**, which opens a popup on `/api/v1/mail/oauth2/authorize?provider=google`.
2. Workspace redirects that popup to Google's consent screen with `access_type=offline` and `prompt=consent`, so Google always returns a **refresh token**.
3. Google sends the browser back to `https://<your-domain>/mail/oauth2/callback` with an authorization code.
4. Workspace exchanges the code for tokens, reads the mailbox address from Google's userinfo endpoint, and creates the account pre-filled with `imap.gmail.com:993` (SSL) and `smtp.gmail.com:587` (STARTTLS).
5. Tokens are encrypted at rest and used to authenticate every IMAP and SMTP session with the `XOAUTH2` mechanism. Access tokens are refreshed automatically, 60 seconds before expiry.

If Google ever refuses a refresh (token revoked, consent withdrawn, password reset), the account is deactivated and its owner receives a high-priority notification asking them to reconnect.

## 1. Create a Google Cloud project

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project (or reuse one). The project only holds the OAuth client; it costs nothing and needs no billing account.
2. In **APIs & Services > Library**, enable the **Gmail API**. Workspace talks IMAP and SMTP rather than the Gmail REST API, but enabling it is what makes the `https://mail.google.com/` scope selectable in the consent screen editor.

## 2. Choose the audience

Everything hinges on one Google rule: **`https://mail.google.com/` is a *restricted* scope**. Google gates it much harder than a plain profile scope, and the gate differs depending on who your users are. Pick the row that matches your instance:

| Audience | Who can connect | What Google requires | Refresh token lifetime |
|---|---|---|---|
| **Internal** (Google Workspace organizations only) | Any account in your Workspace domain | Nothing: no verification, no consent warning | Unlimited |
| **External + Testing** | Only the accounts you list as test users (up to 100) | Nothing, but users see an "unverified app" warning | **7 days**, then every account must be reconnected |
| **External + Published** | Anyone with a Google account | App verification **and** an annual CASA security assessment, because of the restricted scope | Unlimited |

For a self-hosted instance the practical choices are the first two. If you have a Google Workspace domain, choose **Internal** and you are done. If you are on personal Gmail accounts, **External + Testing** works fine for a handful of users, as long as you accept that connections have to be re-established weekly - it is Google's rule for unpublished apps, not a Workspace limitation.

Configure it in **APIs & Services > OAuth consent screen** (recent consoles surface it as **Google Auth Platform**, sections *Branding*, *Audience*, *Clients*, *Data Access*):

1. **Branding**: app name (users see it on the consent screen), user support email, developer contact email.
2. **Audience**: Internal or External. If External, add every mailbox you intend to connect under **Test users** - an account missing from that list gets `Error 403: access_denied` with no further explanation.
3. **Data Access > Add or remove scopes**: add exactly the three scopes Workspace requests.

```
https://mail.google.com/
openid
email
```

`https://mail.google.com/` is usually not in the picker list; use **Manually add scopes** and paste it. The scope string is hard-coded in `workspace/mail/services/oauth2.py`, so requesting fewer scopes in the console will simply make the consent screen fail.

## 3. Create the OAuth client

In **APIs & Services > Credentials > Create credentials > OAuth client ID**:

- **Application type**: Web application. Not "Desktop app" - the flow is browser-based and needs a registered redirect URI.
- **Name**: anything, it is only shown in the console.
- **Authorized redirect URIs**: add the callback of your instance, spelled exactly as below.

| Deployment | Redirect URI |
|---|---|
| Production | `https://workspace.example.com/mail/oauth2/callback` |
| Local development | `http://localhost:8000/mail/oauth2/callback` |

Three details Google is strict about:

- **No trailing slash.** `/mail/oauth2/callback/` is a different URI and will be rejected.
- **HTTPS is mandatory**, with a single exception: `http://localhost` (and `http://127.0.0.1`) are accepted for development. A LAN address such as `http://192.168.1.20:8000` is not.
- **The host and port must match what Django itself builds.** Workspace never lets you configure the callback: it derives it from the incoming request. See [Behind a reverse proxy](#behind-a-reverse-proxy) if yours is fronted by nginx, Traefik or Cloudflare.

Google shows the **client ID** and **client secret** once the client is created. The secret can be re-read later from the same page.

## 4. Configure Workspace

Set the two variables, then restart. Both the **web** process and the **Celery worker** need them: the worker is what syncs mailboxes in the background, and refreshing an expired access token requires the client credentials.

**Local development** - in `.env` at the repository root:

```env
OAUTH_GOOGLE_CLIENT_ID=1234567890-abcdef.apps.googleusercontent.com
OAUTH_GOOGLE_CLIENT_SECRET=GOCSPX-your-secret
```

**Docker Compose** - in the `.env` file next to `docker-compose.yml` (both variables are already wired through to the `web` and `worker` services):

```bash
docker compose up -d
```

**Kubernetes** - in `secrets.yaml`, which already carries both keys:

```yaml
stringData:
  OAUTH_GOOGLE_CLIENT_ID: "1234567890-abcdef.apps.googleusercontent.com"
  OAUTH_GOOGLE_CLIENT_SECRET: "GOCSPX-your-secret"
```

```bash
kubectl apply -f secrets.yaml
kubectl -n workspace rollout restart deployment workspace
```

Only providers with a non-empty client ID are offered in the UI, so an instance that leaves these unset simply never shows the Google button.

## 5. Connect a mailbox

1. Open **Mail** and click **Add account**.
2. Click **Connect with Google**. A 600x700 popup opens on Google's consent screen - allow popups for your domain if nothing happens.
3. Pick the account, accept the consent screen (on an External app in testing, click through the "Google hasn't verified this app" warning via *Advanced > Go to ...*).
4. The popup closes on its own and the account appears in the list, already configured for IMAP and SMTP. The first sync starts immediately.

Reconnecting an account that was disconnected uses the very same button: Workspace matches on the mailbox address and refreshes the stored tokens in place rather than creating a duplicate.

## Behind a reverse proxy

The redirect URI is built from the request, so a proxy that rewrites the `Host` header or terminates TLS without saying so produces a callback that does not match what you registered - and Google answers `redirect_uri_mismatch`. Make sure the proxy forwards:

- `Host` (or `X-Forwarded-Host` together with `USE_X_FORWARDED_HOST=1`)
- `X-Forwarded-Proto: https`
- `X-Forwarded-Port`, plus `USE_X_FORWARDED_PORT=1`, when the public port differs from the one Gunicorn sees

See [the reverse proxy section of the deployment overview](../deployments/README.md#reverse-proxy) for the full header list and the security warning that comes with `SECURE_PROXY_SSL_HEADER`.

To see the URI Workspace actually sends, open the Google consent popup and read the `redirect_uri` query parameter of its URL. That value, character for character, is what must appear in the Google console.

## Security notes

- **The client secret is instance-wide.** It never reaches a browser, and no user needs it. Treat it like `SECRET_KEY`: keep it out of version control, and rotate it in the Google console if it leaks (existing connections keep working until their next token refresh, then need reconnecting).
- **Tokens are encrypted at rest** with a Fernet key derived from Django's `SECRET_KEY`. Consequence worth remembering: **changing `SECRET_KEY` makes every stored OAuth2 token and IMAP password undecryptable**, and all mail accounts have to be reconnected.
- **Revoking access is done on Google's side**, from [the account's Third-party apps page](https://myaccount.google.com/connections). The next sync then fails with `invalid_grant`, and Workspace deactivates the account and notifies its owner instead of retrying forever.
- **IMAP access can still be blocked by a Workspace administrator** (*Admin console > Apps > Google Workspace > Gmail > End User Access*), and organizations using API access control must additionally allowlist the OAuth client ID. Personal Gmail accounts have no such switch: IMAP is always available.

## Troubleshooting

**`Error 400: redirect_uri_mismatch`.** The URI Workspace sent is not registered. Read the actual value from the `redirect_uri` parameter of the consent popup URL, then compare it to the console entry - scheme, host, port, and the absence of a trailing slash all count. Behind a proxy, see the section above.

**`Error 403: access_denied`, no consent screen.** The account is not in the **Test users** list of an External app in testing, or an organization policy blocks the app. Add the mailbox as a test user, or switch the audience to Internal if you own the Workspace domain.

**"Google hasn't verified this app".** Expected for an External app that is not published. *Advanced > Go to ...* works; publishing it away requires Google's verification plus a CASA assessment, because of the restricted scope.

**The Connect with Google button is missing.** `OAUTH_GOOGLE_CLIENT_ID` did not reach the process. Confirm with `docker compose exec web env | grep OAUTH_GOOGLE`, and remember that a quoted value in a `.env` file keeps its quotes in some shells. The provider list is rendered server-side, so a restart is required after setting it.

**"Failed to exchange authorization code".** The client secret is wrong, belongs to a different client ID, or the exchange happened after the code expired (codes are single-use and short-lived - retry from scratch). The app log carries the underlying error from Google.

**Everything works, then breaks after a week.** Classic symptom of an External app still in **Testing**: Google expires those refresh tokens after 7 days. Users get the "account disconnected" notification and reconnect with one click; making it permanent means moving to Internal, or publishing and going through verification.

**`No refresh_token for <address>` in the logs.** Google only issues a refresh token on an explicit consent. Workspace already forces this with `access_type=offline` and `prompt=consent`, so hitting this means the account row predates the OAuth2 flow or was created by hand. Reconnect it through the Google button.

**Sync fails with `AUTHENTICATIONFAILED` right after connecting.** The scope set is incomplete: `https://mail.google.com/` is what grants IMAP and SMTP access, and a consent granted before it was added stays limited. Add the scope in the console, then reconnect the account so a new consent is requested.
