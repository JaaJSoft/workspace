# Deployment

This directory contains deployment configurations for Workspace.

## Deployment Modes

| Mode | Directory | Database | Best for |
|------|-----------|----------|----------|
| [Docker Compose](docker-compose/) | `docker-compose/` | SQLite | Single-node, small teams |
| [Kubernetes](kubernetes/) | `kubernetes/` | SQLite (single pod) | Cluster environments |

## Quick Start

### Docker Compose (simplest)

```bash
cd docker-compose/
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

### Kubernetes

```bash
cd kubernetes/
kubectl apply -f namespace.yaml
kubectl apply -f secrets.yaml -f configmap.yaml
kubectl apply -f app.yaml
kubectl apply -f ingress.yaml
```

## Common Notes

- **SQLite**: Both setups default to SQLite. Set `DATABASE_URL` to a PostgreSQL connection string to switch.
- **Vector search** (similar faces and other "find the closest" features): indexed on SQLite out of the box through the bundled `sqlite-vec` extension. On PostgreSQL it is indexed when the server has [pgvector](https://github.com/pgvector/pgvector) - use the `pgvector/pgvector` image instead of `postgres`, and connect as a role allowed to create the extension (a superuser) the first time migrations run. A stock PostgreSQL image works too: the migrations apply, and searches scan each user's vectors instead of using an index, which stays fast for a personal library. `python manage.py check --database default` reports which one you are on (`common.W002`). After adding pgvector to an existing installation, run `python manage.py rebuild_vector_index` once.
- **SECRET_KEY**: Always change the default secret key before deploying.
- **Static files**: Collected at image build time via `collectstatic` and served by WhiteNoise.
- **Metrics**: `/metrics` requires HTTP Basic credentials and answers `401` until `METRICS_USER` and `METRICS_PASSWORD` are both set. See [Monitoring with Prometheus](../guides/monitoring.md).

## Object Storage (S3-compatible)

Uploaded files live under `MEDIA_ROOT` by default. Set `STORAGE_BACKEND=s3` to keep them in a bucket on any S3-compatible store instead - AWS S3, Garage, SeaweedFS, Ceph, Cloudflare R2, Backblaze B2 and the like. The bucket mirrors what `MEDIA_ROOT` would hold, key for key (`files/users/alice/Documents/report.pdf`), so it can be read and synced on its own, and the data does not depend on the database to make sense.

| Variable                  | Default   | Effect |
|---------------------------|-----------|--------|
| `STORAGE_BACKEND`         | `local`   | `local` (`MEDIA_ROOT`) or `s3` |
| `S3_BUCKET`               | -         | Bucket name, required with `s3`. The bucket must exist |
| `S3_ENDPOINT_URL`         | AWS       | URL of the S3 API for any other store (e.g. `http://garage:3900`) |
| `S3_REGION`               | -         | Region, when the store wants one |
| `S3_ACCESS_KEY_ID`        | -         | Access key. Unset to use boto3's own chain (`AWS_*` variables, instance role) |
| `S3_SECRET_ACCESS_KEY`    | -         | Secret key, with the above |
| `S3_PREFIX`               | *(empty)* | Key prefix, so several instances can share one bucket |
| `S3_ADDRESSING_STYLE`     | `auto`    | `path` for most self-hosted stores |
| `S3_PRESIGN_ENDPOINT_URL` | `S3_ENDPOINT_URL` | Public URL of the store, when clients reach it under another name than the app does |
| `S3_CONDITIONAL_WRITES`   | `1`       | Writes that must not replace an existing object send `If-None-Match`. Set to `0` only for a store that rejects it |
| `S3_SIGNED_URLS`          | `0`       | `1` redirects downloads to short-lived signed URLs on the store instead of streaming them through the app (see below) |
| `S3_SIGNED_URL_TTL`       | `3600`    | Seconds a signed download URL stays valid. Long enough for a video to play to its end |

The credentials need `s3:ListBucket` on the bucket and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` and `s3:AbortMultipartUpload` on its objects.

**Add a lifecycle rule that aborts incomplete multipart uploads** after a day or so. Large uploads go in parts and an interrupted one is aborted, but a worker killed in the middle of an upload cannot abort it, and most stores keep (and bill) the parts until told otherwise.

### Downloads served by the store

By default the app streams every download out of the bucket itself, a window at a time. With `S3_SIGNED_URLS=1` it checks access as usual, then redirects the browser to a signed URL on the store, which serves the bytes (and video seeking) directly. The store must then be reachable by the browsers, at `S3_PRESIGN_ENDPOINT_URL` if the app reaches it under another name, and the bucket needs a CORS rule letting the app's origin `GET` - the image viewer `fetch()`es the picture it edits:

```json
[{"AllowedOrigins": ["https://workspace.example.com"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]
```

A signed URL works for whoever holds it until it expires; it is only handed out after the access checks.

### Moving an existing instance to a bucket

The layout is the same on both sides, so moving is a copy. With the app stopped (or at least with nobody uploading):

1. Keep `STORAGE_BACKEND=local` and set `S3_BUCKET` and the other `S3_*` variables.
2. `python manage.py copy_blobs --to s3` copies every blob from `MEDIA_ROOT` to the bucket - and only the blobs: the SQLite database and the model weights stay where they are. It skips what is already there with the same size, so it can run again to catch up, and `--dry-run` shows what it would do.
3. Set `STORAGE_BACKEND=s3` and start the app.
4. `python manage.py verify_file_storage` checks that every file's blob is in the bucket at its place in the tree. `--fix-dirs` recreates the folders a copy left without a directory (an empty folder copied by a tool that skips empty directories).

`copy_blobs --to local` goes the other way. Keep the old copy until the new one has served for a while: neither command deletes anything.

What changes on object storage:

- Renaming or moving a folder copies each of its files inside the store (no bytes go through the app) and then deletes the originals, so it takes longer on a large folder than on a disk.
- The periodic sync that picks up files dropped into `MEDIA_ROOT` by hand is not scheduled; the on-demand sync in the files view still runs.
- The data volume still holds the SQLite database, if you use it, and the face detection model weights (`PHOTOS_MODEL_DIR`).

## Reverse Proxy

Workspace expects to run behind a TLS-terminating reverse proxy (nginx, Caddy, Traefik, an ingress controller, etc.). The application speaks plain HTTP/1.1 on port 8000 - the proxy handles TLS, HTTP/2, HTTP/3, compression, and rate limiting as the operator sees fit.

### Required headers from the proxy

| Header              | Purpose                                                  |
|---------------------|----------------------------------------------------------|
| `X-Forwarded-Proto` | Tells Django the original request was HTTPS              |
| `X-Forwarded-For`   | Real client IP - only believed once `NUM_PROXIES` is set, see below |
| `Host`              | The public hostname (must match `ALLOWED_HOSTS`)         |

### Optional settings for proxies that rewrite Host/Port

Some proxies - notably Cloudflare, AWS ALB, GCP Load Balancer, Azure Front Door - rewrite the `Host` header and forward the original under `X-Forwarded-Host`. Enable these only when you know your proxy does that:

| Variable               | Default | Effect                                                            |
|------------------------|---------|-------------------------------------------------------------------|
| `USE_X_FORWARDED_HOST` | off     | Django trusts `X-Forwarded-Host` instead of `Host`                |
| `USE_X_FORWARDED_PORT` | off     | Django trusts `X-Forwarded-Port` instead of the connection port   |

### Per-IP rate limits behind a proxy

Some endpoints limit by client IP as well as by user. Behind a proxy every request arrives from the
proxy's address, so `X-Forwarded-For` is ignored - a caller-supplied header would buy a fresh bucket
per request - until you declare how many hops are in front:

| Variable      | Default | Effect                                                            |
|---------------|---------|--------------------------------------------------------------------|
| `NUM_PROXIES` | unset   | Hops between client and app; the peer address is used when unset  |

Leaving it unset behind a proxy is safe but blunt: every user shares one bucket, so a busy instance
answers legitimate people with `429`. Set it to the length of your chain, and make sure that chain
**overwrites** `X-Forwarded-For` rather than appending to it.

On `POST /api/v1/csp-report` that bluntness costs more than a `429`, because the endpoint is
unauthenticated and the caller is a browser reporting a Content-Security-Policy violation. Sharing
one bucket across the deployment means ordinary traffic can exhaust it, and anyone able to reach the
endpoint can exhaust it deliberately. A refused report is dropped silently at both ends - the
browser does not retry and nothing is logged - so the effect is that violation reporting stops
without any symptom to notice. Set `NUM_PROXIES` on any deployment that relies on those reports.

### ⚠️ Deploying without a reverse proxy is unsafe

In production (`DEBUG=0`), Workspace sets `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`. **This is only safe when a reverse proxy is in front _and_ that proxy strips any incoming `X-Forwarded-Proto` header from the client.** If Gunicorn is exposed directly to the internet, a malicious client can forge `X-Forwarded-Proto: https` and bypass HTTPS-only checks (secure cookies, HSTS, redirects). Always run behind a proxy in production.
