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

Beyond the standard `django_prometheus` series (HTTP requests by view, method and status, response latency, DB query counts, cache hits/misses), Workspace publishes:

| Metric                                        | Type      | Labels                | Meaning                                          |
|-----------------------------------------------|-----------|-----------------------|--------------------------------------------------|
| `files_upload_bytes_total`                    | counter   | —                     | Bytes of file content received from clients      |
| `files_download_bytes_total`                  | counter   | —                     | Bytes of file content sent to clients            |
| `files_thumbnail_generation_duration_seconds` | histogram | `mime_family`         | Time of one thumbnail generation                 |
| `files_thumbnail_generation_total`            | counter   | `result`              | Outcomes: success / failed / skipped             |
| `ai_request_duration_seconds`                 | histogram | `model`, `status`     | Wall-clock time of one LLM completion call       |
| `ai_tokens_total`                             | counter   | `model`, `kind`       | Tokens reported by the API (prompt / completion) |
| `ai_image_requests_total`                     | counter   | `model`, `op`, `status` | Image generation and edit requests             |
| `ai_agent_checkins_total`                     | counter   | `outcome`             | Agent goal check-ins, by what they produced      |
| `ai_tool_calls_total`                         | counter   | `tool`, `status`      | Tool calls, by outcome (ok/error/repeat)         |
| `ai_tool_rounds`                              | histogram | `model`               | Rounds of tool calls spent on one reply          |
| `ai_tool_loop_stops_total`                    | counter   | `reason`              | Replies cut short by the round cap or a repeat loop |
| `celery_task_duration_seconds`                | histogram | `task`                | Time between task start and completion           |
| `celery_tasks_total`                          | counter   | `task`, `state`       | Executions by final state (success/failure/retry)|
| `celery_queue_length`                         | gauge     | `queue`               | Pending messages in each broker queue            |
| `sse_active_connections`                      | gauge     | —                     | Live Server-Sent Events connections              |
| `sse_events_emitted_total`                    | counter   | `provider`, `event`   | SSE events pushed to clients                     |
| `sse_provider_poll_duration_seconds`          | histogram | `provider`            | Time spent inside one provider poll              |
| `sse_forced_reconnects_total`                 | counter   | `transport`           | Streams closed on the connection budget          |
| `sse_pubsub_messages_total`                   | counter   | —                     | Redis Pub/Sub messages on the per-user channel   |

`celery_queue_length` requires Redis as the broker; it reports nothing with the in-memory broker.

Series recorded inside Celery tasks stay in the worker process, which serves no `/metrics` of its own: `celery_task_duration_seconds`, `celery_tasks_total`, `ai_agent_checkins_total`, the thumbnail and malware scan series, and whatever share of the AI series a background task produces. They are exported by the web process, but only with the values the web process recorded itself.

## Multiple Gunicorn workers

The container runs several Gunicorn workers (`GUNICORN_WORKERS`), each a separate process with its own values. So that a scrape reports the whole instance rather than whichever worker answered it, the image runs `prometheus_client` in multiprocess mode, with no setup on your side:

- At startup, Gunicorn creates an empty directory under `/tmp` and every worker writes its values there, one file per process. `/metrics` sums them. The directory is removed when Gunicorn exits.
- When a worker dies, its counters stay in the totals and `sse_active_connections` stops counting its streams.
- To put the files somewhere else, a tmpfs mount for instance, set `PROMETHEUS_MULTIPROC_DIR`. Workspace empties that directory at startup, so do not share it between containers.

Unlike a single-process run such as `manage.py runserver`, the process and garbage-collector series (`process_*`, `python_gc_*`) are not exposed: they have no multiprocess form.

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

**Counters jump around between scrapes.** Each scrape is reporting a single worker. That happens when Gunicorn starts without the image's `gunicorn.conf.py`, for example from a custom command that drops `-c gunicorn.conf.py`. Keep the flag, and see [Multiple Gunicorn workers](#multiple-gunicorn-workers).

**404 instead of 401.** The path has no trailing slash: `/metrics`, not `/metrics/`.
