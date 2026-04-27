# ── Base ──────────────────────────────────────────────────────────────────────
# Design decision: python:3.12-slim gives us the latest CPython with a small
# image footprint.  We don't need build tools after installation, so we clean
# up apt caches in the same RUN layer to keep layers small.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── API target ────────────────────────────────────────────────────────────────
FROM base AS api

COPY . .

# Non-root user for security.
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ── Worker target ─────────────────────────────────────────────────────────────
FROM base AS worker

COPY . .

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# CMD is overridden in docker-compose.yml so we can pass worker flags cleanly.
CMD ["celery", "-A", "app.tasks.review_tasks.celery_app", "worker", "--loglevel=info"]


# ── Dashboard target ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS dashboard

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY dashboard/ ./dashboard/
COPY dashboard/rxconfig.py .

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

EXPOSE 3000
CMD ["reflex", "run", "--env", "prod"]
