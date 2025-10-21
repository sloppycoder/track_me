# stage 1: build
FROM python:3.12-bookworm AS builder
LABEL org.opencontainers.image.source=https://github.com/owner/track_me
LABEL org.opencontainers.image.description="visualize geo locations from my photos"

# access token for private repos
ARG PAT="dummy"

# install packages needed by python packages
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

# install uv
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /uvx /usr/local/bin/

RUN if  [ -n "$PAT" -a "$PAT" != "dummy" ]; then \
        echo "Configuring Git with PAT for private repo access..."; \
        git config --global url."https://x-access-token:${PAT}@github.com/sloppycoder/".insteadOf "https://github.com/sloppycoder/"; \
    else \
        echo "No PAT provided. Skipping Git configuration for private repos."; \
    fi

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy
# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --all-extras

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --all-extras

# stage 2: runtime
FROM python:3.12-slim-bookworm

# create non-root user
RUN groupadd --system --gid 999 appuser \
 && useradd --system --gid 999 --uid 999 appuser

USER appuser

WORKDIR /app

# copy virtualenv from builder
COPY --chown=appuser:appuser --from=builder /app/.venv /app/.venv
# copy application code. update .dockerignore to files that shouldn't be copied
COPY --chown=appuser:appuser . .

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

CMD  ["/bin/sh", "/app/entrypoint.sh"]

