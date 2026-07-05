# syntax=docker/dockerfile:1
# --------------------------------------------------------------------------- #
# stage 1: build a self-contained virtualenv with uv                          #
# --------------------------------------------------------------------------- #
FROM python:3.12-bookworm AS builder
LABEL org.opencontainers.image.source="https://github.com/sloppycoder/track_me"
LABEL org.opencontainers.image.description="track_me travel-timeline viewer"

# uv: fast, lockfile-driven installer. Copy the static binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Build (and later run) from a single fixed path. uv installs the project as an
# editable install pointing at /app/src, so the source must live at this same
# path in the runtime stage — hence /app here and /app there.
WORKDIR /app

# Resolve deps first (cached until the lockfile changes), then install the project.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --------------------------------------------------------------------------- #
# stage 2: slim runtime                                                       #
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm

# unprivileged runtime user
RUN addgroup --system app && adduser --system --group app
WORKDIR /app

# venv + source together, at the SAME /app path they were built at, so the
# editable install resolves and package data (templates, schema.sql) is present.
COPY --chown=app:app --from=builder /app /app

ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

USER app
EXPOSE 8080

# Cloud Run sets $PORT (default 8080). Serve the Flask WSGI app with gunicorn.
# Shell form so $PORT expands. A single worker keeps the SQLite / gcsfuse reads
# simple; bump --workers/--threads if you need more concurrency.
CMD exec gunicorn --bind ":$PORT" --workers 1 --threads 8 --timeout 60 \
    track_me.viewer.app:app
