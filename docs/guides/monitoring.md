# Monitoring with Prometheus

Workspace exposes Prometheus metrics at **`/metrics`** and Kubernetes health probes under `/health/`. This page covers what is exposed, how to open the endpoint to your scraper, and how to wire it up in each deployment mode.

## Authentication

`/metrics` is protected by **HTTP Basic auth** and stays closed until you configure it:

| Variable           | Description                                                        |
|--------------------|--------------------------------------------------------------------|
| `METRICS_USER`     | Username the scraper must present                                  |
| `METRICS_PASSWORD` | Password, in **plain text** — never base64-encode it here          |

If either is empty, every request gets `401` and an error is logged. An unconfigured instance never serves its metrics to anyone.

The base64 you see in an `Authorization: Basic …` header is Basic auth's transport encoding, applied by the client and undone by the server. It is not a secret format: **only expose `/metrics` over HTTPS or inside a trusted network.**

> **Why not an IP allowlist?** Only `REMOTE_ADDR` can be trusted, and behind a reverse proxy that is always the proxy's own address — an allowlist would grant access to every client reaching the proxy. Credentials do not have that failure mode.

Access is all-or-nothing: there is no per-user or read-only variant, and a logged-in Workspace user (superuser included) gets no implicit access. The credentials are for the scraper.

## Prometheus scrape config

```yaml
scrape_configs:
  - job_name: workspace
    metrics_path: /metrics
    scheme: https
    basic_auth:
      username: prometheus
      password: <METRICS_PASSWORD>   # plain text, matches the env var
    static_configs:
      - targets: ["workspace.example.com"]
```

Prefer `password_file` over an inline `password` if your Prometheus config is stored in version control.

## Docker Compose

Set both variables in the `.env` file next to `docker-compose.yml`:

```env
METRICS_USER=prometheus
METRICS_PASSWORD=a-long-random-password
```

They are already passed through to the `web` service in `docker-compose.yml`. Restart to apply:

```bash
docker compose up -d
```

If Prometheus runs in the same compose network, target the service directly (`http://web:8000/metrics`) so credentials never leave the network — though they are still required.

## Kubernetes

`METRICS_USER` lives in `configmap.yaml` and `METRICS_PASSWORD` in `secrets.yaml`:

```yaml
# secrets.yaml — stringData takes plain text, no base64
stringData:
  METRICS_PASSWORD: "a-long-random-password"
```

Note the distinction: a Secret's `stringData:` block takes plain text (Kubernetes encodes it for you), while a `data:` block requires base64. The provided manifest uses `stringData`.

Apply and restart so the pod picks up the change:

```bash
kubectl apply -f secrets.yaml -f configmap.yaml
kubectl -n workspace rollout restart deployment workspace
```

`ingress.yaml` additionally drops `/metrics` at the edge with a `deny all` snippet. That is defence in depth on top of the credentials — in-cluster scraping goes through the ClusterIP Service and bypasses the ingress, so it keeps working:

```yaml
- job_name: workspace
  basic_auth:
    username: prometheus
    password: <METRICS_PASSWORD>
  static_configs:
    - targets: ["workspace-web.workspace.svc:80"]
```

Remove the ingress snippet if you scrape from outside the cluster.

## What is exposed

Beyond the standard `django_prometheus` series (HTTP requests by view, method and status, response latency, DB query counts, cache hits/misses, migration state) and the default Python process metrics, Workspace publishes:

| Metric                                        | Type      | Labels                | Meaning                                          |
|-----------------------------------------------|-----------|-----------------------|--------------------------------------------------|
| `files_upload_bytes_total`                    | counter   | —                     | Bytes of file content received from clients      |
| `files_download_bytes_total`                  | counter   | —                     | Bytes of file content sent to clients            |
| `files_thumbnail_generation_duration_seconds` | histogram | `mime_family`         | Time of one thumbnail generation                 |
| `files_thumbnail_generation_total`            | counter   | `result`              | Outcomes: success / failed / skipped             |
| `ai_request_duration_seconds`                 | histogram | `model`, `status`     | Wall-clock time of one LLM completion call       |
| `ai_tokens_total`                             | counter   | `model`, `kind`       | Tokens reported by the API (prompt / completion) |
| `ai_image_requests_total`                     | counter   | `model`, `op`, `status` | Image generation and edit requests             |
| `celery_task_duration_seconds`                | histogram | `task`                | Time between task start and completion           |
| `celery_tasks_total`                          | counter   | `task`, `state`       | Executions by final state (success/failure/retry)|
| `celery_queue_length`                         | gauge     | `queue`               | Pending messages in each broker queue            |
| `sse_active_connections`                      | gauge     | —                     | Live Server-Sent Events connections              |
| `sse_events_emitted_total`                    | counter   | `provider`, `event`   | SSE events pushed to clients                     |
| `sse_provider_poll_duration_seconds`          | histogram | `provider`            | Time spent inside one provider poll              |
| `sse_forced_reconnects_total`                 | counter   | `transport`           | Streams closed on the connection budget          |
| `sse_pubsub_messages_total`                   | counter   | —                     | Redis Pub/Sub messages on the per-user channel   |

`celery_queue_length` requires Redis as the broker; it reports nothing with the in-memory broker.

## Health probes

Separate from metrics, unauthenticated by design, and used by Kubernetes:

| Endpoint          | Checks                                   |
|-------------------|------------------------------------------|
| `/health/startup` | Database reachable                       |
| `/health/live`    | Process responsive                       |
| `/health/ready`   | Database **and** cache available         |

`/health/ready` answers `500` with a per-component breakdown when something is down.

## Troubleshooting

**Every scrape returns 401.** Check the app logs. `METRICS_USER and METRICS_PASSWORD are unset` means the variables never reached the container — with Compose, confirm the `.env` file sits next to `docker-compose.yml`; on Kubernetes, confirm the pod was restarted after applying the Secret. A `Rejected /metrics request from …` line instead means the credentials arrived but did not match.

**Credentials look right but still 401.** Leading and trailing whitespace is trimmed from both variables, so a stray newline in a secret file is not the cause — but a quoted value is: `METRICS_PASSWORD="secret"` in a `.env` file keeps the quotes as part of the password in some shells. Compare against what the app received with `docker compose exec web env | grep METRICS`.

**Counters jump around between scrapes.** Under Gunicorn each worker keeps its own registry and a scrape lands on one worker at random, so values appear to move backwards. Fixing it means running `prometheus_client` in multiprocess mode (`PROMETHEUS_MULTIPROC_DIR` plus a Gunicorn `child_exit` hook to reap dead workers); Workspace ships no such configuration by default. Rate-based queries over a single-worker deployment are unaffected.

**404 instead of 401.** The path has no trailing slash: `/metrics`, not `/metrics/`.
