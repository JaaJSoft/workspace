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
- **Metrics**: `/metrics` requires HTTP Basic credentials and answers `401` until `METRICS_USER` and `METRICS_PASSWORD` are both set. See [Monitoring with Prometheus](../guides/monitoring.md).

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

A few endpoints carry a per-IP limit on top of their per-user one, so that abuse spread across
several stolen session cookies is still visible. Behind a proxy every request arrives from the
proxy's own address, and `X-Forwarded-For` is the only thing naming the real client - but that
header is written by the caller, and a different value per request would hand out a fresh limit
bucket every time. It is therefore ignored unless you declare how many hops are in front:

| Variable      | Default | Effect                                                                  |
|---------------|---------|-------------------------------------------------------------------------|
| `NUM_PROXIES` | unset   | Number of proxies between client and app; the peer address is used when unset |

**Set it if you run behind a proxy.** Leaving it unset there is safe but blunt: every user shares
the proxy's single bucket, so a busy instance will hand legitimate people `429` responses. Set it
to the length of your proxy chain, and make sure that chain **overwrites** `X-Forwarded-For` rather
than appending to whatever the client sent - otherwise the hop you end up trusting is the client's
own invention.

### ⚠️ Deploying without a reverse proxy is unsafe

In production (`DEBUG=0`), Workspace sets `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`. **This is only safe when a reverse proxy is in front _and_ that proxy strips any incoming `X-Forwarded-Proto` header from the client.** If Gunicorn is exposed directly to the internet, a malicious client can forge `X-Forwarded-Proto: https` and bypass HTTPS-only checks (secure cookies, HSTS, redirects). Always run behind a proxy in production.
