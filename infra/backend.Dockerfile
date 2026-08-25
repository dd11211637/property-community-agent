FROM python:3.11-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
RUN mkdir -p src/property_agent \
    && touch src/property_agent/__init__.py
RUN python -m pip install --no-cache-dir .

FROM dependencies AS base
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts/setup_langgraph_checkpointer.py ./scripts/setup_langgraph_checkpointer.py

EXPOSE 8000

FROM base AS runtime
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app
CMD ["python", "-m", "uvicorn", "property_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS demo-support
COPY testing ./testing

FROM dependencies AS test
RUN python -m pip install --no-cache-dir ".[dev]"
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY testing ./testing
COPY tests ./tests
COPY frontend/e2e ./frontend/e2e
CMD ["python", "-m", "pytest", "-rs"]

# Keep the default build target production-only.  Compose selects ``test`` or
# ``demo-support`` explicitly for those workflows.
FROM runtime AS production
