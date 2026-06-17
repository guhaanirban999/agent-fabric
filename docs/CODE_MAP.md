# Code Map — every source file mapped to its layer (L1–L5)

A file-by-file index of the codebase, grouped by the five layers from
[ARCHITECTURE_LAYERS.md](ARCHITECTURE_LAYERS.md) (L1 experience → L5 data). The shared library
`libs/fabric_common` is cross-cutting, so its submodules are listed separately and mapped to the
layer each primarily serves. Infrastructure and tooling that spans all layers is listed at the end.

```
L1 Experience    services/chatbot/**            (+ planned services/console)
L2 Orchestration services/broker/**
L3 Governance    services/gateway_mcp/**  services/gateway_a2a/**  policies/authz.rego
L4 Control Plane services/registry/**
L5 Data&Backends fixtures/**                     (+ Postgres — infra, no code)
Shared lib       libs/fabric_common/**           (per-submodule mapping below)
Infra & tooling  Dockerfile  docker-compose.yml  pyproject.toml  scripts/  tests/
```

---

## L1 — Experience / Interface

| File | Role |
|---|---|
| `services/chatbot/chatbot_svc/main.py` | Slack `slack-bolt` AsyncApp + Socket Mode handler; bridges Slack → broker `/chat` |
| `services/chatbot/chatbot_svc/broker_client.py` | httpx client that calls the broker `POST /chat` |
| `services/chatbot/chatbot_svc/dedupe.py` | Slack event-dedupe cache (ignore duplicate event deliveries) |
| `services/chatbot/chatbot_svc/__init__.py` | package marker |
| `services/chatbot/pyproject.toml` | chatbot package + deps (slack-bolt) |

> _Planned:_ `services/console/**` — the onboarding UI BFF (not yet in the repo).

---

## L2 — Orchestration / Brokerage  (`services/broker`)

| File | Role |
|---|---|
| `services/broker/broker_svc/main.py` | FastAPI app; `POST /tasks`, `POST /tasks/stream` (SSE), `POST /chat` |
| `services/broker/broker_svc/router.py` | LLM router — Anthropic SDK forced tool-use `select_routes`, enum-constrained agent ids |
| `services/broker/broker_svc/orchestrator.py` | `/tasks` pipeline: submit → registry candidates → route → dispatch → persist |
| `services/broker/broker_svc/chat_orchestrator.py` | `/chat` pipeline: history → route (force=False) → dispatch → synthesize reply |
| `services/broker/broker_svc/dispatch.py` | best-first dispatch through the gateways (MCP via fastmcp, A2A via JSON-RPC) + next-best retry |
| `services/broker/broker_svc/store.py` | `broker_tasks` persistence (asyncpg) → writes to L5 Postgres |
| `services/broker/broker_svc/conversation_store.py` | `broker_conversations` multi-turn memory (asyncpg) → writes to L5 Postgres |
| `services/broker/broker_svc/__init__.py` | package marker |
| `services/broker/pyproject.toml` | broker package + deps (anthropic, sse-starlette, fastmcp) |

---

## L3 — Governance / Gateway

| File | Role |
|---|---|
| `services/gateway_mcp/gateway_mcp_svc/main.py` | FastAPI app; `/mcp` composite, `POST /admin/reload`, `GET /audit` |
| `services/gateway_mcp/gateway_mcp_svc/proxy.py` | FastMCP `create_proxy` + `mount` of registered MCP backends; reload/MountManager |
| `services/gateway_mcp/gateway_mcp_svc/governance.py` | middleware enforcing `on_call_tool` (deny→ToolError) + per-subject `on_list_tools` filtering |
| `services/gateway_mcp/pyproject.toml` | gateway-mcp package + deps (fastmcp) |
| `services/gateway_a2a/gateway_a2a_svc/main.py` | FastAPI app; `POST /a2a/{id}`, `GET /audit` |
| `services/gateway_a2a/gateway_a2a_svc/handler.py` | resolves agent from registry per-request, applies policy, reverse-proxies JSON-RPC/SSE |
| `services/gateway_a2a/pyproject.toml` | gateway-a2a package + deps |
| `policies/authz.rego` | the OPA **policy** (Rego v1): allow-lists, rate limits, redaction, fail-closed default |

_See also the shared PEP under **Shared library → governance** below — both gateways call it._

---

## L4 — Control Plane / Registry  (`services/registry`)

| File | Role |
|---|---|
| `services/registry/registry_svc/main.py` | FastAPI app + lifespan (schema create_all, launch prober); `/healthz`, fabric card |
| `services/registry/registry_svc/api.py` | HTTP routes: `POST/GET/DELETE /agents`, `/agents/{id}`, `/card`, `/heartbeat`, `/domains` |
| `services/registry/registry_svc/service.py` | registration logic — MCP introspection vs A2A card fetch; `RegistrationError` |
| `services/registry/registry_svc/cards.py` | `introspect_mcp` (tools/list → synthetic card), A2A card fetch/parse/verify, skill extraction |
| `services/registry/registry_svc/probe.py` | health prober (~30s loop): MCP `list_tools` / A2A GET card → flip health |
| `services/registry/registry_svc/repository.py` | data-access: create/list/get/delete entries, heartbeat, domains |
| `services/registry/registry_svc/orm.py` | SQLAlchemy ORM table (`AgentEntryORM`) |
| `services/registry/registry_svc/db.py` | async engine, `Base`, `get_session` dependency |
| `services/registry/registry_svc/settings.py` | registry-local settings (e.g. heartbeat interval) |
| `services/registry/registry_svc/__init__.py` | package marker |
| `services/registry/alembic/env.py` | Alembic migration environment (production schema path) |
| `services/registry/alembic/versions/0001_initial.py` | initial migration |
| `services/registry/pyproject.toml` | registry package + deps (sqlalchemy, asyncpg, alembic) |

---

## L5 — Data & Backends

| File | Role |
|---|---|
| `fixtures/echo_mcp/echo_mcp/server.py` | sample **MCP** server (FastMCP) — tools `echo`/`reverse`/`add`/`danger` |
| `fixtures/echo_a2a/echo_a2a/server.py` | sample **A2A** agent (a2a-sdk) — `echo` skill |
| `fixtures/writer_a2a/writer_a2a/server.py` | LLM **A2A** agent (a2a-sdk + Anthropic SDK) — `assist` skill |
| `fixtures/*/__init__.py`, `fixtures/*/pyproject.toml` | package markers + per-fixture deps |

> **Postgres** is the durable store for this layer (`registry` rows, `audit_log`, `broker_tasks`,
> `broker_conversations`) — it's the `postgres` service in `docker-compose.yml`, no code file.
> The live **MuleSoft CloudHub** MCP server (`mule-products`) is remote and registered at runtime,
> so it has no file here either.

---

## Shared library — `libs/fabric_common` (cross-cutting)

Imported by every service. Each submodule is mapped to the layer it primarily serves.

| File | Serves | Role |
|---|---|---|
| `fabric_common/models/entries.py` | L4 | `RegisterRequest`, `AgentEntry`, `AgentSkillSummary`, `AuthScheme`, enums |
| `fabric_common/models/governance.py` | L3 | `PolicyInput`, `PolicyDecision`, `AuditRecord` |
| `fabric_common/models/tasks.py` | L2 | task / chat request-response models |
| `fabric_common/models/__init__.py` | — | re-exports |
| `fabric_common/governance/pep.py` | L3 | Policy Enforcement Point — orchestrates OPA + rate-limit + audit + span (fail-closed) |
| `fabric_common/governance/opa.py` | L3 | OPA client (`POST /v1/data/fabric/authz`) |
| `fabric_common/governance/ratelimit.py` | L3 | token-bucket rate limiter |
| `fabric_common/governance/audit.py` | L3 | asyncpg audit sink → `audit_log` table |
| `fabric_common/registry_client/client.py` | L4 client | `RegistryClient` used by broker (L2) + gateways (L3) to query the registry |
| `fabric_common/auth/jwt.py` | cross-cutting | OIDC/JWT validation (`JWTValidator`) |
| `fabric_common/auth/tokens.py` | cross-cutting | on-behalf-of token mint/verify (`mint_downstream_token`/`verify_internal_token`) |
| `fabric_common/telemetry/otel.py` | cross-cutting | OpenTelemetry setup + FastAPI instrumentation |
| `fabric_common/config.py` | cross-cutting | `pydantic-settings` `Settings` (DB url, OPA url, service URLs, model, secrets) |
| `fabric_common/*/__init__.py`, `pyproject.toml` | — | package markers + lib deps |

---

## Infrastructure & tooling (spans all layers)

| File | Role |
|---|---|
| `Dockerfile` | one fat image: `uv sync --all-packages` installs every workspace member into one venv |
| `docker-compose.yml` | single-host stack; each service picks its entrypoint via `command` (postgres, opa, fixtures, registry, gateways, broker, chatbot, writer-a2a) |
| `pyproject.toml` (root) | uv workspace declaration (`libs/*`, `services/*`, `fixtures/*`) + dev deps |
| `scripts/test.sh` | runs the pytest suite inside the compose network |
| `tests/conftest.py` | shared fixtures; idempotently registers the fixtures (incl. writer-a2a) |
| `tests/test_contract.py` | registry descriptor-shape contract tests |
| `tests/test_e2e.py` | gateway governance + broker routing + SSE + the governed `assist` path |
| `tests/test_auth.py` | OBO token helper unit tests |
| `tests/pytest.ini` | pytest config |
