# Agent Fabric — Operations Runbook

Single-host (Docker Compose) operations for the open-source Agent Fabric.

## Service map

| Service | Host port | Role |
|---|---|---|
| registry | 8000 | agent/MCP/A2A catalog + discovery + health prober |
| gateway-mcp | 8010 | governed MCP endpoint (`/mcp`), `/admin/reload`, `/audit` |
| gateway-a2a | 8011 | governed A2A proxy (`/a2a/{id}`), `/audit` |
| broker | 8020 | NL task routing + dispatch (`/tasks`, `/tasks/stream`) |
| opa | 8181 | policy decision point (hot-reload via `--watch`) |
| postgres | 5432 | registry, audit_log, broker_tasks |
| otel-collector | 4317/4318 | traces + metrics (console exporter) |
| echo-mcp / echo-a2a | 9001 / 9002 | sample fixtures (echo tool / echo agent) |
| writer-a2a | 9003 | LLM-backed A2A writing assistant (skill `assist`, domain `assistant`) |

## Lifecycle

```bash
cp .env.example .env          # first run; set ANTHROPIC_API_KEY for the broker
docker compose up -d --build  # start everything
docker compose ps             # status
docker compose logs -f broker # tail a service
docker compose down           # stop (add -v to wipe postgres volume)
```

Health: `curl localhost:8000/healthz` (and 8010/8011/8020).

## Onboarding agents

```bash
# MCP server (registry introspects tools/list -> synthetic card)
curl -X POST localhost:8000/agents -H 'content-type: application/json' -d '{
  "kind":"mcp_server","name":"my-api","domain":"sales",
  "endpoint_url":"http://my-mcp:9001/mcp","transport":"streamable-http"}'

# A2A agent (registry fetches /.well-known/agent-card.json)
curl -X POST localhost:8000/agents -H 'content-type: application/json' -d '{
  "kind":"a2a_agent","domain":"sales","card_url":"http://my-agent:8080"}'

# After registering a NEW MCP server, refresh the gateway (no restart):
curl -X POST localhost:8010/admin/reload
# (A2A agents need no gateway reload — gateway-a2a resolves them per-request.)
```

Discovery: `GET /agents?domain=&kind=&skill=&tag=&health=`, `GET /domains`.

### Adding a native A2A agent (e.g. an LLM assistant)

`writer-a2a` is a worked example of a self-contained native A2A agent — onboarding is
code-additive only, no registry/gateway/broker changes:

1. Add a fixture under `fixtures/<name>/` modeled on `fixtures/writer_a2a` (a2a-sdk route
   builders + an `AgentExecutor`; publishes `/.well-known/agent-card.json`).
2. Add a compose service using the `<<: *app` anchor (it inherits `env_file: .env`, so
   `ANTHROPIC_API_KEY` is available). `docker compose build <name> && docker compose up -d <name>`
   — the fat image bakes the venv, so a **build is required** for the new workspace member.
3. Register it: `curl -X POST localhost:8000/agents -d '{"kind":"a2a_agent","domain":"assistant","card_url":"http://writer-a2a:9003"}'`.
4. Add each skill id to `allowed_a2a_skills` in `policies/authz.rego` (OPA `--watch` hot-reloads).

```bash
# Route a task to the writer agent (LLM summarize/rewrite/answer):
curl -X POST localhost:8020/tasks -H 'content-type: application/json' \
  -d '{"task_text":"summarize in one sentence: <text>","domain":"assistant"}'
```

## Running work

```bash
curl -X POST localhost:8020/tasks -H 'content-type: application/json' \
  -d '{"task_text":"reverse the word fabric","domain":"demo"}'

# streamed progress (SSE: accepted -> candidates -> routed -> dispatching -> completed)
curl -N -X POST localhost:8020/tasks/stream -H 'content-type: application/json' \
  -d '{"task_text":"add 2 and 3"}'
```

## Governance

- Policy lives in `policies/authz.rego` (bind-mounted). OPA runs with `--watch`, so
  **edits hot-reload** — no restart. Verify a decision directly:
  ```bash
  curl -X POST localhost:8181/v1/data/fabric/authz -d '{"input":{"subject":{"sub":"anonymous","scopes":["*"]},"protocol":"mcp","action":"mcp.call_tool","tool":"reverse","data_classes":[]}}'
  ```
- **Fail-closed**: if OPA is unreachable, calls are denied and audited as
  `opa-unreachable-fail-closed`.
- Audit trail (shared `audit_log` table, both gateways): `curl localhost:8010/audit?limit=20`.

## Observability

- Traces/metrics flow to otel-collector (console exporter): `docker compose logs otel-collector`.
  A single trace id spans broker → gateway → downstream and appears in `audit_log.trace_id`.
- To back a Visualizer later, swap the collector's `debug` exporter for Tempo/Jaeger in
  `deploy/otel-collector-config.yaml` and point Grafana at it.

## Tests

```bash
docker compose run --rm -v "$PWD/tests:/tests" --no-deps broker \
  bash -c "uv pip install -q --python /app/.venv/bin/python pytest pytest-asyncio && \
           cd /tests && /app/.venv/bin/python -m pytest"
```
Contract tests validate registry shapes (incl. the `writer-a2a` `assist` skill); e2e tests
cover gateway governance (allow/deny/list-filter), A2A allow/deny, the governed LLM `assist`
path, broker routing, and SSE streaming; unit tests cover the OBO token helper. 16 tests total.
(e2e broker + `assist` tests make real Anthropic calls — needs `ANTHROPIC_API_KEY`.)

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Gateway denies everything (`opa-unreachable-fail-closed`) | OPA down or policy parse error. `docker compose logs opa`; validate with `docker compose run --rm --entrypoint opa opa check /policies`. Use image `openpolicyagent/opa:1.7.1` (NOT `-rootless`). |
| New MCP server not callable via gateway | `curl -X POST localhost:8010/admin/reload` (gateway loads backends at startup). |
| Broker returns 503 | `ANTHROPIC_API_KEY` not set in `.env`; `docker compose up -d broker`. |
| A2A call returns `VERSION_NOT_SUPPORTED` | Send header `A2A-Version: 1.0`. |
| Registry duplicate entries | Re-registration creates a new row (no upsert yet); `DELETE /agents/{id}` to clean up. |

## Remaining production hardening (not yet wired)

- **Auth on**: set `OIDC_ISSUER` / `OIDC_JWKS_URL` in `.env`. Gateways then validate
  inbound JWTs (`fabric_common.auth.JWTValidator`). For downstream propagation, mint an
  OBO token with `fabric_common.auth.mint_downstream_token(...)` and attach it as the
  downstream `Authorization` header (helper + tests exist; gateway wiring is the last step).
- **TLS ingress**: front the stack with Traefik (TLS terminator + single ingress); no
  policy logic moves there — the Python gateways keep semantic enforcement.
- **Scale-out**: move the rate-limiter from in-process to Redis; run multiple gateway
  replicas behind the ingress.
- **Migrations**: switch the registry from startup `create_all` to `alembic upgrade head`.
