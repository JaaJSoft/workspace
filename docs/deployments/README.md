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

## Processes

Every container runs the same image and the same command, `run.sh`. `PROCESS_TYPE` picks what it starts:

| `PROCESS_TYPE`  | Starts                          | How many per deployment |
|-----------------|---------------------------------|-------------------------|
| `web` (default) | Gunicorn, with gevent workers   | any                     |
| `worker`        | A Celery worker                 | one or more             |
| `beat`          | The Celery scheduler            | exactly one             |

A single-node deployment can do without the `beat` container: `CELERY_WORKER_BEAT=1` runs the scheduler inside the worker. Set it on one worker only, or every periodic task runs once per worker.

### Web

| Variable                    | Default        | Effect                                                     |
|-----------------------------|----------------|------------------------------------------------------------|
| `GUNICORN_WORKERS`          | `3`            | Worker processes. Each serves many requests at once (gevent) and holds its own copy of the app in memory |
| `GUNICORN_BIND`             | `0.0.0.0:8000` | Listening address                                          |
| `GUNICORN_LOG_LEVEL`        | `info`         | Gunicorn log level                                         |
| `GUNICORN_ACCESS_LOGFORMAT` | Apache-like    | Access log format                                          |
| `GUNICORN_CMD_ARGS`         | *(empty)*      | Any other Gunicorn flag, e.g. `--max-requests 1000 --max-requests-jitter 100` |

### Worker

Celery reads every option of `celery worker` from an environment variable: `CELERY_WORKER_` followed by the option's long name in upper case, dashes turned into underscores. `--max-tasks-per-child 100` is `CELERY_WORKER_MAX_TASKS_PER_CHILD=100`. The ones worth knowing:

| Variable                             | Default                       | Effect |
|--------------------------------------|-------------------------------|--------|
| `CELERY_WORKER_CONCURRENCY`          | one per CPU the container may use | Child processes. Each holds its own copy of the libraries and models its tasks load, so memory grows with this number. A CPU limit counts (Kubernetes `limits.cpu`, `docker run --cpus`), not the cores of the host |
| `CELERY_WORKER_AUTOSCALE`            | *(unset)*                     | `max,min`, e.g. `6,1`: up to *max* children under load, back down to *min* when idle, which hands an idle child's memory back. Replaces the concurrency |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | `524288`                      | KiB of resident memory after which a child is replaced, once its current task ends. `0` disables |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD`  | *(unset)*                     | Replace a child after that many tasks |
| `CELERY_WORKER_BEAT`                 | *(unset)*                     | `1` runs the scheduler in this worker (single node, one worker only) |
| `CELERY_WORKER_LOGLEVEL`             | `info`                        | Worker log level |
| `CELERY_WORKER_WITHOUT_GOSSIP`, `CELERY_WORKER_WITHOUT_MINGLE`, `CELERY_WORKER_WITHOUT_HEARTBEAT` | *(unset)* | `1` turns off what workers broadcast to each other and to monitors such as Flower. A lone worker with no monitor needs none of it |

Keep the default `prefork` pool (`CELERY_WORKER_POOL`): several tasks rely on time limits that the `threads` and `solo` pools never enforce.

### Beat

| Variable                        | Default                                    | Effect |
|---------------------------------|--------------------------------------------|--------|
| `CELERY_BEAT_SCHEDULE_FILENAME` | `celerybeat-schedule`, in the working directory | Where the scheduler records when each periodic task last ran. On a volume it survives a restart. Also read by a worker running `CELERY_WORKER_BEAT=1` |
| `CELERY_BEAT_LOGLEVEL`          | `info`                                     | Scheduler log level |

Other `celery beat` options follow the same pattern as the worker's, with the `CELERY_BEAT_` prefix.

### Memory allocator

`run.sh` sets `MALLOC_MMAP_THRESHOLD_=131072` for all three processes. By default glibc keeps the large buffers a request or a task reads (a whole file, a decoded image) on its heap once they are freed. With a fixed threshold, they go back to the system. Set another value in bytes to override it.

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
