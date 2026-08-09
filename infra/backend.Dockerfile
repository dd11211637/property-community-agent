FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY testing ./testing

EXPOSE 8000

FROM base AS test
RUN python -m pip install --no-cache-dir ".[dev]"
COPY tests ./tests
CMD ["python", "-m", "pytest", "-rs"]

FROM base AS runtime
CMD ["python", "-m", "uvicorn", "property_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
