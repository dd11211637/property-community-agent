# Local Release Candidate runbook

This stack builds Frontend V2 as static production assets, places the API and Agent SSE behind one Nginx edge, and starts the backend against persistent PostgreSQL/pgvector with Alembic and the official LangGraph checkpointer. It binds only the selected localhost port, does not switch production traffic, and keeps public v2 rollout at zero.

## Required configuration

Set these values without writing them to the repository or shell history:

- `RC_POSTGRES_PASSWORD`, `JWT_SECRET`, and `DEEPSEEK_API_KEY`;
- `RELEASE_SHA` equal to `git rev-parse HEAD`;
- a running local Ollama with `qwen3-embedding:0.6b`, or explicit Memory provider
  URL/key/model/version/source-dimension overrides;
- optional `RC_HTTP_PORT`.

Put these values in an ignored `.env` file or inject them through the shell. `JWT_SECRET` must have at least 32 characters. OTel is disabled by default for local use. The default stack never runs files under `testing/`.

## First start

```powershell
.\scripts\local_rc.ps1 bootstrap
.\scripts\local_rc.ps1 start
.\scripts\local_rc.ps1 status
```

The helper reads the ignored `.env` from this or another worktree of the same repository, injects the exact current `HEAD`, and never prints secrets. Pass `-EnvFile C:\path\to\.env` only when automatic discovery is unsuitable.

The product is served from `http://127.0.0.1:8080/`. `/api/agent/conversations/{id}/messages/stream` is a real POST SSE path with proxy buffering disabled and a bounded long-read timeout. The production image omits `demo.html`.

Before the first start, install the default local embedding model with
`ollama pull qwen3-embedding:0.6b`. The RC allows 30 seconds for a cold model
load and stores its padded 1536-dimensional vectors in pgvector.

## One-time local bootstrap

On a new database, explicitly create localhost starter identities and representative records, then start the product. This profile is never part of the default startup chain.

The `bootstrap` command is idempotent and only needs to run again after creating an empty database.

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
.\scripts\local_rc.ps1 stop
.\scripts\local_rc.ps1 start
.\scripts\local_rc.ps1 restart
.\scripts\local_rc.ps1 status
```

`down`, `stop`, and `restart` preserve the named `rc_postgres` volume. Never add `--volumes` unless permanently deleting local product data is intended. Back up that volume before destructive Docker cleanup. Protected certification and production readiness are deliberately deferred.
