# Build from the repository root: docker build -f backend/Dockerfile -t aiops-backend:local .
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    AIOPS_DATABASE_URL=sqlite:////data/aiops.db AIOPS_LOG_DIR=/logs/backend \
    LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY backend/ backend/
COPY agent/ agent/
COPY diagnostic-service/ diagnostic-service/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN groupadd --gid 10001 aiops && useradd --uid 10001 --gid aiops --no-create-home aiops \
    && mkdir /data /logs && chown aiops:aiops /data /logs
USER 10001:10001
EXPOSE 8005
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8005", "--no-access-log"]
