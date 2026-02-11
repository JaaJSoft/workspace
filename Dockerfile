# Dockerfile
# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies (--no-install-project to skip building the project itself)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Runtime
FROM python:3.14-slim

# OCI metadata — static labels
LABEL org.opencontainers.image.title="workspace" \
      org.opencontainers.image.description="JaaJSoft workspace project" \
      org.opencontainers.image.url="https://github.com/JaaJSoft/workspace" \
      org.opencontainers.image.source="https://github.com/JaaJSoft/workspace" \
      org.opencontainers.image.vendor="JaaJSoft" \
      org.opencontainers.image.licenses="BSL" \
      org.opencontainers.image.base.name="docker.io/library/python:3.14-slim"

# OCI metadata — dynamic labels (overridden at build time by CI)
ARG VERSION=dev
ARG REVISION=""
ARG CREATED=""
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

# System deps required at runtime (cairosvg needs libcairo)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy the code
COPY --chown=appuser:appuser . .

# Collect static files in the image
ENV DJANGO_SETTINGS_MODULE=workspace.settings
RUN SECRET_KEY=build-secret DEBUG=0 python manage.py collectstatic --noinput

# Gunicorn workers count and logs configurable (default values)
ENV GUNICORN_WORKERS=3 \
    GUNICORN_LOG_LEVEL=info \
    GUNICORN_ACCESS_LOGFORMAT="%(h)s %(l)s %(u)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" %(D)s"

USER appuser
EXPOSE 8000

# Start command (exec replaces shell so gunicorn receives signals as PID 1)
CMD ["sh", "-c", "exec gunicorn workspace.wsgi:application -b 0.0.0.0:8000 -w ${GUNICORN_WORKERS} --log-level ${GUNICORN_LOG_LEVEL} --error-logfile - --access-logfile - --access-logformat \"${GUNICORN_ACCESS_LOGFORMAT}\" --capture-output"]
