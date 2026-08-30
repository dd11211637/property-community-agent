# Local Release Candidate runbook

This stack builds Frontend V2 as static production assets, places the API and Agent SSE behind one Nginx edge, and starts the backend against persistent PostgreSQL/pgvector with Alembic and the official LangGraph checkpointer. It binds only the selected localhost port, does not switch production traffic, and keeps public v2 rollout at zero.

## Required configuration

Set these values without writing them to the repository or shell history:

- `RC_POSTGRES_PASSWORD`, `JWT_SECRET`, and `DEEPSEEK_API_KEY`;
- `RELEASE_SHA` equal to `git rev-parse HEAD`;
- optional `MEMORY_EMBEDDING_API_KEY` for semantic Memory retrieval;
- optional provider URLs/models and `RC_HTTP_PORT`.

Put these values in an ignored `.env` file or inject them through the shell. `JWT_SECRET` must have at least 32 characters. OTel is disabled by default for local use. The default stack never runs files under `testing/`.

## Build and start

```powershell
$env:RELEASE_SHA = (git rev-parse HEAD).Trim()
docker compose -f compose.rc.yaml config --quiet
docker compose -f compose.rc.yaml up --build -d frontend-v2
docker compose -f compose.rc.yaml ps
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/ready
```

The product is served from `http://127.0.0.1:8080/`. `/api/agent/conversations/{id}/messages/stream` is a real POST SSE path with proxy buffering disabled and a bounded long-read timeout. The production image omits `demo.html`.

## One-time local bootstrap

On a new database, explicitly create localhost starter identities and representative records, then start the product. This profile is never part of the default startup chain.

```powershell
$env:RELEASE_SHA = (git rev-parse HEAD).Trim()
docker compose -f compose.rc.yaml --profile local-bootstrap run --build --rm local-bootstrap
docker compose -f compose.rc.yaml up --build -d frontend-v2
```

Sign in at `http://127.0.0.1:8080/` with `zhangsan` / `123456`. Other role accounts are documented in `testing/seeds/seed_platform.py`; change or replace these localhost-only starter credentials before exposing the service beyond loopback.

## Isolated acceptance profile

The acceptance profile uses a tmpfs PostgreSQL database and deterministic test identities. It exercises production application code and real HTTP/SSE through the same Frontend V2 image; fixtures remain under `testing/` and are never a product startup dependency.

```powershell
$env:RELEASE_SHA = (git rev-parse HEAD).Trim()
$env:RC_E2E_BASE_URL = "http://127.0.0.1:18080"
docker compose -f compose.rc.yaml --profile acceptance up --build -d --wait acceptance-frontend
Push-Location frontend-v2
try {
  npm ci
  npx playwright install chromium
  npm run test:real
}
finally {
  Pop-Location
  docker compose -f compose.rc.yaml --profile acceptance down --volumes
}
```

The main Compose file requires the production variables even when only the acceptance profile is selected. For CI acceptance, inject non-secret unused placeholders for those variables; the acceptance services override them with isolated-test values.

## Stop, restart, and data retention

```powershell
docker compose -f compose.rc.yaml logs --tail 200 backend frontend-v2
docker compose -f compose.rc.yaml restart backend
Invoke-RestMethod http://127.0.0.1:8080/ready
docker compose -f compose.rc.yaml down
docker compose -f compose.rc.yaml up -d frontend-v2
```

`down`, `stop`, and `restart` preserve the named `rc_postgres` volume. Never add `--volumes` unless permanently deleting local product data is intended. Back up that volume before destructive Docker cleanup. Protected certification and production readiness are deliberately deferred.
