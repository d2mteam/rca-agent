# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS codex
RUN npm install --global @openai/codex@0.164.0

FROM docker:29.6.2-cli AS docker-cli
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim-bookworm AS runtime
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex
WORKDIR /opt/hitl
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PATH="/opt/hitl/.venv/bin:${PATH}" \
    HITL_ROOT=/opt/hitl HITL_HOST=127.0.0.1 HITL_PORT=8765 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
COPY pyproject.toml uv.lock ./
COPY backend/hitl ./backend/hitl
RUN uv sync --locked --no-dev --no-editable
COPY examples ./examples
COPY --from=frontend /build/dist ./frontend/dist
CMD ["infra", "serve"]
