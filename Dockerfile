# Dockerfile
# Stage 1: CSS builder (Tailwind + DaisyUI + Typography)
# Runs npm + tailwindcss to produce workspace/common/static/css/app.css.
# Always rebuilds in CI so a stale committed CSS file can never ship to prod.
FROM node:26-bookworm-slim AS css-builder

WORKDIR /build

# Install npm deps first (cache layer that survives template edits)
COPY scripts/tailwind/package.json scripts/tailwind/package-lock.json ./scripts/tailwind/
RUN cd scripts/tailwind && npm ci --omit=optional

# Then copy the inputs Tailwind scans (entry CSS, config, templates, JS)
COPY scripts/tailwind/input.css scripts/tailwind/tailwind.config.js ./scripts/tailwind/
COPY workspace/ ./workspace/
RUN cd scripts/tailwind && npm run build:css

# Stage 2: Python builder
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies (--no-install-project to skip building the project itself)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Stage 3: Runtime
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

# System deps required at runtime (cairosvg needs libcairo, video frames need ffmpeg).
# `apt-get upgrade` pulls pending Debian security patches without waiting for the
# python:3.14-slim base image to be rebuilt with them.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy the code
COPY --chown=appuser:appuser . .

# Overlay the freshly-built Tailwind CSS on top of whatever was committed.
# The committed file is for local-dev convenience; CI always regenerates it.
COPY --from=css-builder --chown=appuser:appuser \
    /build/workspace/common/static/css/app.css \
    /app/workspace/common/static/css/app.css

# /app itself is owned by root (created by WORKDIR), so appuser cannot create
# subdirectories in it. Hand the working tree over to appuser before switching
# user, otherwise `collectstatic` fails to mkdir /app/staticfiles.
RUN chown appuser:appuser /app

# Run as non-root from this point on so collectstatic creates /app/staticfiles
# with the correct owner. FILE_UPLOAD_DIRECTORY_PERMISSIONS=0o700 means a
# directory created by root cannot be read by appuser at runtime.
USER appuser

# Collect static files in the image
ENV DJANGO_SETTINGS_MODULE=workspace.settings
RUN SECRET_KEY=build-secret DEBUG=0 python manage.py collectstatic --noinput

# Gunicorn workers count and logs configurable (default values)
ENV GUNICORN_WORKERS=3 \
    GUNICORN_LOG_LEVEL=info \
    GUNICORN_ACCESS_LOGFORMAT="%(h)s %(l)s %(u)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" %(D)s"
EXPOSE 8000

# Start command (exec replaces shell so gunicorn receives signals as PID 1)
CMD ["sh", "-c", "exec gunicorn workspace.wsgi:application -b 0.0.0.0:8000 -k gevent -w ${GUNICORN_WORKERS} --log-level ${GUNICORN_LOG_LEVEL} --error-logfile - --access-logfile - --access-logformat \"${GUNICORN_ACCESS_LOGFORMAT}\" --capture-output"]
