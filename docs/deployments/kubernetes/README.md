# Kubernetes Deployment

Single-pod deployment using SQLite and plain Kubernetes manifests.

## Architecture

```
Pod workspace (×1, Recreate)
┌─────────┬─────────┬─────────┬─────────┐
│   web   │ worker  │  beat   │  redis  │
│ gunicorn│ celery  │ celery  │  :6379  │
│  :8000  │         │         │         │
└────┴────┴────┴────┴─────────┴─────────┘
     │         │
     ▼         ▼
  PVC /app/data (SQLite + user files)
```

All containers run in a single pod:

| Container  | Role                             | Image                             |
|------------|----------------------------------|-----------------------------------|
| **web**    | Django/Gunicorn HTTP server      | `ghcr.io/jaajsoft/workspace:main` |
| **worker** | Celery worker (background tasks) | `ghcr.io/jaajsoft/workspace:main` |
| **beat**   | Celery beat (task scheduler)     | `ghcr.io/jaajsoft/workspace:main` |
| **redis**  | Cache and Celery broker          | `redis:7-alpine`                  |

An **initContainer** (`migrate`) runs database migrations before the pod starts.

## Files

| File             | Description                                                    |
|------------------|----------------------------------------------------------------|
| `namespace.yaml` | Namespace `workspace`                                          |
| `secrets.yaml`   | Sensitive config: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `WEBPUSH_VAPID_PRIVATE_KEY` |
| `configmap.yaml` | Non-sensitive config: debug, allowed hosts, workers, log level |
| `app.yaml`       | Deployment (all containers) + PVC + Service                    |
| `ingress.yaml`   | Ingress (nginx) with TLS                                       |

## Prerequisites

- A Kubernetes cluster with an **nginx ingress controller**
- A TLS secret `workspace-tls` in the `workspace` namespace (or use cert-manager)
- Access to `ghcr.io/jaajsoft/workspace` images

## Deploy

```bash
# 1. Create namespace
kubectl apply -f namespace.yaml

# 2. Configure secrets and config
#    Edit secrets.yaml: set a real SECRET_KEY
#    Edit configmap.yaml: set ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS to your domain
#    Edit ingress.yaml: replace workspace.example.com with your domain
kubectl apply -f secrets.yaml
kubectl apply -f configmap.yaml

# 3. Deploy the application
kubectl apply -f app.yaml

# 4. Wait for the pod to be ready
kubectl -n workspace wait --for=condition=ready pod -l app=workspace --timeout=120s

# 5. Expose via ingress
kubectl apply -f ingress.yaml
```

## Configuration

### Secrets (`secrets.yaml`)

| Key            | Description                                                           |
|----------------|-----------------------------------------------------------------------|
| `SECRET_KEY`              | Django secret key. **Must be changed** before deploying.              |
| `DATABASE_URL`            | Database connection string. Default: `sqlite:////app/data/db.sqlite3` |
| `REDIS_URL`               | Redis connection. Default: `redis://localhost:6379/0` (sidecar)       |
| `WEBPUSH_VAPID_PRIVATE_KEY` | VAPID private key (PEM). Generate with `manage.py generate_vapid_keys` |

### ConfigMap (`configmap.yaml`)

| Key                    | Default                         | Description                               |
|------------------------|---------------------------------|-------------------------------------------|
| `DEBUG`                | `0`                             | Set to `1` to enable debug mode           |
| `ALLOWED_HOSTS`        | `workspace.example.com`         | Comma-separated list of allowed hostnames |
| `CSRF_TRUSTED_ORIGINS` | `https://workspace.example.com` | Comma-separated list of trusted origins   |
| `GUNICORN_WORKERS`     | `3`                             | Number of Gunicorn workers                |
| `GUNICORN_LOG_LEVEL`   | `info`                          | Gunicorn log level                        |
| `DJANGO_LOG_LEVEL`     | `INFO`                          | Django log level                          |
| `TRASH_RETENTION_DAYS` | `30`                            | Days before trashed files are purged      |
| `MEDIA_ROOT`           | `/app/data`                     | Root directory for user files and uploads |
| `WEBPUSH_VAPID_PUBLIC_KEY` | *(empty)*                  | VAPID public key (base64url)              |
| `WEBPUSH_VAPID_MAILTO` | *(empty)*                       | Contact email for VAPID claims (`mailto:…`) |

## Storage

A single `PersistentVolumeClaim` (`workspace-data`, 10Gi, `ReadWriteOnce`) is mounted at `/app/data` and contains:

- The SQLite database (`db.sqlite3`)
- User-uploaded files

## Health Checks

The web container exposes `/health/` with three probes:

- **startupProbe**: polls every 2s, up to 30 failures (allows slow boot)
- **livenessProbe**: every 15s
- **readinessProbe**: every 5s

## Scaling Limitations

This setup uses **SQLite** and runs as a **single replica** (`Recreate` strategy). It is not horizontally scalable. To scale beyond one pod, switch to PostgreSQL and separate the worker/beat into their own deployments.
