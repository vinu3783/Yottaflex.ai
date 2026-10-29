# Image used for both the mock API and the pipeline CLI.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Dependencies first (cached layer), exactly as pinned in uv.lock.
# Dev tools (pytest, ruff) are included so the test suite can run in the container.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY tests ./tests
COPY sql ./sql
COPY scripts ./scripts
RUN uv sync --frozen

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

CMD ["ingest", "--help"]
