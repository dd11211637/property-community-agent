FROM python:3.11-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# 构建期网络适配：默认使用国内 PyPI 镜像源，避免容器内直连 PyPI 时 TLS 被重置。
# 生产/CI 如需官方源可覆盖：docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

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
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
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
