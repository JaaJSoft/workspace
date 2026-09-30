# Sending email from the instance

Workspace can send mail **on its own behalf**: security alerts, password
resets, address verifications, notification fallbacks. This is unrelated to
the Mail module, which sends as each user through their own mailbox and needs
nothing from this page.

Out of the box nothing is configured and **the instance sends no email at
all**: every attempt is logged and dropped, and features that would send tell
the user why they can't. Everything below is opt-in.

## 1. Point the instance at a relay

Use an SMTP relay rather than delivering straight to recipients' mail servers:
a transactional provider (Amazon SES, Postmark, Mailgun, SendGrid, Brevo...),
your organisation's mail server, or a local Postfix that relays to one. A relay
handles the retries, the per-domain throttling and the IP reputation that
direct delivery would leave to you, and most residential or cloud IPs are
blocklisted for direct delivery anyway.

```bash
EMAIL_HOST=smtp.relay.example           # Setting it turns sending on
EMAIL_PORT=587                          # Default; 465 when EMAIL_USE_SSL is on
EMAIL_USE_TLS=true                      # STARTTLS, the default
# EMAIL_USE_SSL=true                    # Implicit TLS instead (port 465)
EMAIL_HOST_USER=workspace
EMAIL_HOST_PASSWORD=...
EMAIL_TIMEOUT=15                        # Seconds per SMTP operation

DEFAULT_FROM_EMAIL="Workspace <noreply@notify.example.com>"
SERVER_EMAIL="Workspace ops <ops@example.com>"   # Django's own error mails
EMAIL_REPLY_TO=support@example.com      # Optional, comma-separated
EMAIL_BASE_URL=https://workspace.example.com     # Public origin, for links in mails
```

| Variable | Default | Meaning |
|---|---|---|
| `EMAIL_HOST` | empty | SMTP relay. Empty keeps sending off. |
| `EMAIL_BACKEND` | derived | Any Django email backend path. Derived from `EMAIL_HOST`: SMTP when set, the console backend in development, a backend that sends nothing otherwise. |
| `EMAIL_ENABLED` | derived | Force sending off (`false`) while keeping a relay configured. |
| `EMAIL_BASE_URL` | empty | Public origin of the instance. Without it only transactional mail is sent - notification mail needs a working unsubscribe link. |
| `EMAIL_RATE_LIMIT_PER_RECIPIENT` | `20` | Mails accepted per address per rolling hour. |
| `EMAIL_RATE_LIMIT_GLOBAL` | `500` | Mails accepted for the whole instance per rolling hour. Keep it under your relay's own quota. |
| `EMAIL_DELIVERY_RETENTION_DAYS` | `90` | Days a send record is kept. |
| `ANYMAIL_WEBHOOK_SECRET` | empty | `user:password` the provider sends to the bounce webhooks (see below). Empty keeps them off. |
| `ANYMAIL_<NAME>` | - | Any other variable with this prefix reaches [django-anymail](https://anymail.dev) as `ANYMAIL["<NAME>"]`. |

The SMTP password, the webhook secret and the providers' API keys and tokens
are redacted from logs and from error reports. Keep them in your secret store
like any other credential.

### Through a provider's API instead of SMTP

Every provider also takes mail over SMTP, and that is all the setup above
needs. [django-anymail](https://anymail.dev/en/stable/esps/) can send through
the provider's HTTP API instead - point `EMAIL_BACKEND` at its backend and pass
the provider's credentials as `ANYMAIL_*` variables, named as in anymail's page
for that provider:

```bash
EMAIL_BACKEND=anymail.backends.postmark.EmailBackend
ANYMAIL_POSTMARK_SERVER_TOKEN=...
```

The API is not faster for a single mail, but the provider then hands back its
own id for each message, and the bounce it reports later is matched to the
exact delivery in the admin. Over SMTP the address is suppressed all the same;
only the link to the original delivery is missing.

Delivery always happens in a Celery worker: a slow or unreachable relay never
holds up a page. A mail the relay refuses temporarily (a 4xx reply, a timeout,
a dropped connection) is retried five times over about half an hour; a
permanent refusal (5xx) is recorded as failed at once.

### Check it works

In the admin dashboard, the **Outgoing email** panel has a **Send a test
email** button. It mails the address of the signed-in administrator through the
normal path and shows the result once the worker has handled it (reload the
page): *sent*, or the relay's exact error. Every mail the instance sends, test
or not, is listed under **Email > Deliveries** with its feature, status and
error.

## 2. DNS for the sending domain

Mailbox providers (Gmail, Outlook, Yahoo) reject or spam-folder mail that is
not authenticated. Publish three records for the domain in `DEFAULT_FROM_EMAIL`
- in the examples, `notify.example.com`. Your relay's documentation gives the
exact values; the shapes are always these.

**SPF** - which servers may send for the domain. One TXT record on the domain
itself, naming the relay:

```
notify.example.com.  TXT  "v=spf1 include:spf.relay.example -all"
```

**DKIM** - the relay signs every message with a key whose public half you
publish under a *selector*. The relay tells you the selector and the key; the
record lives at `<selector>._domainkey.<domain>`:

```
wksp2026._domainkey.notify.example.com.  TXT  "v=DKIM1; k=rsa; p=MIIBIjANBgkq..."
```

(Many relays give you a CNAME to their own key instead - same place, same
effect, and they rotate the key for you.)

**DMARC** - what receivers do when SPF or DKIM fail, and where they send
reports. Start with `p=none` to collect reports, move to `quarantine` then
`reject` once the reports show only your relay:

```
_dmarc.notify.example.com.  TXT  "v=DMARC1; p=none; rua=mailto:dmarc-reports@example.com; adkim=s; aspf=r"
```

**Alignment** - DMARC passes only if the domain of the `From:` header matches
the domain that DKIM signs for (`d=` in the signature) or the SPF envelope
domain. Configure the relay to sign with `d=notify.example.com` - its "custom
domain" or "domain authentication" setting - and use the same domain in
`DEFAULT_FROM_EMAIL`. A relay signing with its own domain leaves you
unaligned, and DMARC fails however good the SPF record is.

Check a real message with the "Show original" view in Gmail: `SPF: PASS`,
`DKIM: PASS` and `DMARC: PASS` should all be there.

## 3. Separate streams, separate reputations

Mailbox providers score reputation per domain. A burst of complaints about
notifications must not send password resets to spam, so give each stream its
own subdomain, each with its own SPF, DKIM and DMARC:

| Stream | Example From | Content |
|---|---|---|
| Transactional | `noreply@account.example.com` | Password resets, verifications, security alerts - mail the user asked for |
| Notifications | `noreply@notify.example.com` | Digests, mentions, reminders - everything that carries an unsubscribe link |

Most relays also let you put each stream on its own IP pool or "message
stream". A new domain has no reputation: expect a few weeks of low volume
before the large providers trust it, and do not start with a bulk send.

## 4. Bounces and complaints

Sending again and again to an address that bounces is what ruins a domain's
reputation. The instance keeps a suppression list (**Email > Suppressions** in
the admin) and never mails an address on it again:

- a relay refusing the recipient outright (a 5xx on `RCPT TO`) suppresses the
  address immediately;
- a bounce or complaint the relay learns about later has to be reported to the
  webhooks below;
- a recipient following the unsubscribe link of a notification mail
  suppresses that one kind of mail only - transactional mail still reaches
  them;
- an administrator can add or delete a suppression by hand.

### The bounce webhooks

A provider reports what happened to a mail after it accepted it - the bounce
from the recipient's server, the spam complaint - by calling a URL of yours.
Every provider uses its own format; [django-anymail](https://anymail.dev)
reads them all and the instance suppresses the address, whichever it is.

1. Pick a user name and a long random password, and set
   `ANYMAIL_WEBHOOK_SECRET=user:password`. Until it is set the webhooks answer
   404: open, they would let anyone mark any address as bounced.
2. In the provider's dashboard, add a webhook for bounces and spam complaints
   pointing at your instance, with the credentials in the URL:

   ```
   https://user:password@workspace.example.com/api/v1/email/<provider>/tracking
   ```

   `<provider>` is one of `amazon_ses`, `brevo`, `mailersend`, `mailgun`,
   `mailjet`, `mailtrap`, `mandrill`, `postal`, `postmark`, `resend`,
   `sendgrid`, `sparkpost`, `unisender_go`.
3. Some providers also sign their webhooks (Mailgun, Resend, Postal, Amazon
   SNS...). Anymail's page for the provider names the extra key; pass it as an
   `ANYMAIL_*` variable like the others.

What suppresses an address:

| Reported | Effect |
|---|---|
| Permanent bounce (the address does not exist) | Suppressed for every mail. |
| Spam complaint | Suppressed for every mail. |
| The provider refusing an address it knows is bad | Suppressed for every mail. |
| Temporary failure (full mailbox, DNS error, greylisting) | Nothing - the provider retries those itself. |

A relay you run yourself (a Postfix, an Exchange) has no webhook to call: the
bounces it receives stay in its own mailbox. Refusals it reports during the
SMTP exchange are still suppressed, and an administrator can add any other
address by hand.
