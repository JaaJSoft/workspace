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
- **SECRET_KEY**: Always change the default secret key before deploying.
- **Static files**: Collected at image build time via `collectstatic` and served by WhiteNoise.

## Reverse Proxy

Workspace expects to run behind a TLS-terminating reverse proxy (nginx, Caddy, Traefik, an ingress controller, etc.). The application speaks plain HTTP/1.1 on port 8000 — the proxy handles TLS, HTTP/2, HTTP/3, compression, and rate limiting as the operator sees fit.

### Required headers from the proxy

| Header              | Purpose                                                  |
|---------------------|----------------------------------------------------------|
| `X-Forwarded-Proto` | Tells Django the original request was HTTPS              |
| `X-Forwarded-For`   | Real client IP (used for logs and rate limiting)         |
| `Host`              | The public hostname (must match `ALLOWED_HOSTS`)         |

### Optional settings for proxies that rewrite Host/Port

Some proxies — notably Cloudflare, AWS ALB, GCP Load Balancer, Azure Front Door — rewrite the `Host` header and forward the original under `X-Forwarded-Host`. Enable these only when you know your proxy does that:

| Variable               | Default | Effect                                                            |
|------------------------|---------|-------------------------------------------------------------------|
| `USE_X_FORWARDED_HOST` | off     | Django trusts `X-Forwarded-Host` instead of `Host`                |
| `USE_X_FORWARDED_PORT` | off     | Django trusts `X-Forwarded-Port` instead of the connection port   |

### ⚠️ Deploying without a reverse proxy is unsafe

In production (`DEBUG=0`), Workspace sets `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`. **This is only safe when a reverse proxy is in front _and_ that proxy strips any incoming `X-Forwarded-Proto` header from the client.** If Gunicorn is exposed directly to the internet, a malicious client can forge `X-Forwarded-Proto: https` and bypass HTTPS-only checks (secure cookies, HSTS, redirects). Always run behind a proxy in production.
