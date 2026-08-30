# Full Product Release Candidate runbook

This stack builds Frontend V2 as static production assets, places the API and Agent SSE behind one Nginx edge, and starts the existing backend against PostgreSQL/pgvector with Alembic and the official LangGraph checkpointer. It does not seed product data, switch production traffic, or change the public v2 rollout from zero.

## Required configuration

Set these values without writing them to the repository or shell history:

- `RC_POSTGRES_PASSWORD`, `JWT_SECRET`, `DEEPSEEK_API_KEY`, `MEMORY_EMBEDDING_API_KEY`;
- `RELEASE_SHA` equal to `git rev-parse HEAD`;
- optional provider URLs/models, `RC_HTTP_PORT`, telemetry settings, and `DEPLOYMENT_ENVIRONMENT`.

Accounts, houses, roles, and business records must already be provisioned in the target database. The default stack never runs files under `testing/`.

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

## Operations and shutdown

```powershell
docker compose -f compose.rc.yaml logs --tail 200 backend frontend-v2
docker compose -f compose.rc.yaml restart backend
Invoke-RestMethod http://127.0.0.1:8080/ready
docker compose -f compose.rc.yaml down
```

Do not add `--volumes` to normal shutdown unless destroying the RC database is explicitly intended. Protected certification additionally requires an exact-SHA preproduction deployment, server-owned certification identity, protected credentials, observability evidence, and the repository workflow contract.
