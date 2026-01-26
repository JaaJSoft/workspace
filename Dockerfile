# Dockerfile
# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy the code
COPY . .

# Collect static files in the image (simplifies running)
# These variables will be overridden at runtime via docker-compose/env
ENV DJANGO_SETTINGS_MODULE=workspace.settings \
    SECRET_KEY=build-secret \
    DEBUG=0 \
    ALLOWED_HOSTS="*"

# Gunicorn workers count and logs configurable (default values)
ENV GUNICORN_WORKERS=3 \
    GUNICORN_LOG_LEVEL=info \
    GUNICORN_ACCESS_LOGFORMAT="%(h)s %(l)s %(u)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" %(D)s"

RUN python manage.py collectstatic --noinput

# Permissions and port
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000

# Start command (Gunicorn)
CMD ["sh", "-c", "gunicorn workspace.wsgi:application -b 0.0.0.0:8000 -w ${GUNICORN_WORKERS} --log-level ${GUNICORN_LOG_LEVEL} --error-logfile - --access-logfile - --access-logformat \"${GUNICORN_ACCESS_LOGFORMAT}\" --capture-output"]
