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
