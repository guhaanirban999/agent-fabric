# Open-Source Agent Fabric — MuleSoft Agent Fabric replacement

## Context

The organization wants MuleSoft Agent Fabric's capabilities but has **no Omni Gateway / Agent Fabric license**. Their MuleSoft APIs can already be exposed as **MCP servers**, so the tool layer exists. What's missing — and what this project builds — is the **agent control plane** (the "fabric") in open source.

MuleSoft Agent Fabric is four pillars on top of a licensed gateway:

| Pillar | What it does | The licensed gap |
|---|---|---|
| Agent Registry | Catalog/discovery of agents, MCP & A2A servers (built on A2A Agent Cards) | — |
| Agent Broker | Groups agents into business "domains", routes a task to the best-fit agent/tool | — |
| Agent Visualizer | Topology map + traces *(DEFERRED in our MVP)* | — |
| Agent Governance | Security/compliance/policy guardrails on every interaction | Runs through **Omni Gateway** — the licensed piece we replace |

The two underlying protocols are **fully open**: Google **A2A** (Linux Foundation, `a2a-sdk`) and Anthropic **MCP** (`mcp` / `fastmcp`). Only MuleSoft's control plane + gateway are paid. We rebuild that layer in **Python**, assemble existing OSS frameworks for the broker, deploy via **Docker Compose**, and scope the MVP to **Registry + Gateway/Governance + Broker** (Visualizer deferred, but lightweight OpenTelemetry tracing is included since governance/audit needs it).

## Outcome

A single-host Docker Compose stack where: MCP servers (MuleSoft APIs) and A2A agents self-register into a **Registry**; all MCP/A2A traffic flows through governed **Gateways** that enforce OPA policy + auth + audit + tracing; and a **Broker** takes a natural-language task, routes it to the best registered agent/tool, and dispatches through the gateways.

## Verified stack (mid-2026)

- **A2A**: `a2a-sdk` 1.x (async, Starlette/FastAPI, JSON-RPC + SSE streaming). Agent Cards at `/.well-known/agent-card.json`; v1.0 supports **signed (JWS) cards**. Install `a2a-sdk[fastapi,telemetry,postgresql]`. *Pin exact versions — 0.3→1.0 API churned.*
- **MCP**: official `mcp` (stay on **stable v1.x**, avoid v2 beta) + **`fastmcp` 3.x** for the gateway. FastMCP gives `as_proxy()`, `mount()` (aggregate many MCP servers behind one endpoint), and a **middleware pipeline** (`on_call_tool`/`on_list_tools`) — our policy/audit interception point. Use **Streamable HTTP** transport. Cache `list_tools` (~300-400ms proxied).
- **Broker orchestration**: **Google ADK** (native A2A, agent-as-tool) as the runtime; keep the LLM-router logic in a thin framework-agnostic module so ADK↔LangGraph is swappable.
- **Policy**: **OPA** (Open Policy Agent) as the single PDP sidecar; gateways are PEPs.
- **Tooling**: `uv` workspace monorepo, FastAPI, SQLAlchemy 2.x + asyncpg, pydantic v2, `authlib`/`pyjwt`, OpenTelemetry.

## Service decomposition (Compose)

| Service | Responsibility | Key libs | Main endpoints |
|---|---|---|---|
| **registry** | Catalog of agent/MCP/A2A entries; card storage + JWS-trust validation; discovery; health polling | FastAPI, `a2a-sdk` card models, SQLAlchemy+asyncpg, pydantic | `POST/PUT/GET /agents`, `GET /agents/{id}/card`, `POST /agents/{id}/heartbeat`, `GET /domains`, `/.well-known/agent-card.json` |
| **gateway-mcp** | Proxy+aggregate all MCP servers into one governed Streamable-HTTP endpoint; policy+audit on `tools/call`; per-subject `list_tools` filtering | `fastmcp` (proxy/mount/middleware), httpx | `POST /mcp`, `GET /mcp/servers` |
| **gateway-a2a** | Proxy A2A JSON-RPC + SSE to downstream agents; policy+audit on `message/send`/`message/stream` | a2a-sdk client+server, Starlette, `sse-starlette`, httpx | `POST /a2a/{agent_id}`, `/a2a/{agent_id}/stream` |
| **broker** | Read registry → classify domain → LLM best-fit route → dispatch via gateways | Google ADK, a2a-sdk, mcp/fastmcp client, LLM client | `POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel` |
| **authz (OPA)** | Policy decision point for both gateways | `openpolicyagent/opa` | `POST /v1/data/fabric/authz/allow` |
| **postgres** | Registry, task records, audit log | `postgres:16` | 5432 |
| **otel-collector** | OTLP traces/metrics from all services | `otel/opentelemetry-collector-contrib` | 4317/4318 |
| **idp (dev only)** | Local OIDC for OAuth2 testing | Keycloak / mock OIDC | 8080 |

**Gateway choice — pure-Python, not Envoy/APISIX/Kong.** Policy here is *semantic* (must parse MCP `tools/call` args and A2A message parts to apply data-class/tool-name rules) — far easier in Python (FastMCP middleware + a2a-sdk handlers) than Lua/WASM filters. Optionally add **Traefik** in front purely as TLS terminator/ingress (no policy logic). Revisit Envoy only when outgrowing single-host.

## Design detail

**Registry.** One canonical descriptor — the A2A `AgentCard` — for *all* entry kinds (A2A agents natively; MCP servers wrapped as a synthetic card whose tools become `AgentSkill`s). Entry fields: `id`, `kind` (`a2a_agent|mcp_server|a2a_remote`), `domain` (broker's grouping unit), `tags[]`, `agent_card` JSONB, `endpoint_url`, `transport`, `auth` scheme, `card_jws`+`trusted`, `health`/`last_heartbeat`. Discovery: `GET /agents?domain=&skill=&capability=&kind=&health=`. Self-register (A2A): caller posts card URL → registry fetches `/.well-known/agent-card.json`, validates schema, **verifies JWS** against trusted keys. Operator-register (MCP): admin posts descriptor → registry calls `list_tools` through gateway-mcp → synthesizes card. Stale heartbeats flip entries to `down` (excluded from broker candidates).

**Gateway + Governance.** Shared core in `fabric_common.governance` (OPA client, PEP middleware, rate limiter, audit). MCP: FastMCP `as_proxy`+`mount` composite; middleware `on_call_tool` → extract subject/tool/args → OPA `allow` check → audit + OTel span; `on_list_tools` → per-subject filtering (RBAC = discovery scoping). A2A: parse JSON-RPC envelope, same OPA check (with message-part data-class inspection), proxy SSE without buffering. Rate limits are **policy-driven** (OPA returns the budget). **Auth**: validate inbound OAuth2/OIDC JWTs against IdP JWKS (`authlib`/`pyjwt`); subject+scopes → OPA `input.subject`. **Auth propagation (biggest hazard):** use **token-exchange / on-behalf-of** — gateway mints a downstream token carrying original `sub` + `act` claims; do **not** blind-forward inbound tokens (audience mismatch). **Audit**: every decision + call written to `audit_log` (data-class-aware redaction) *and* emitted as OTel spans sharing one trace id across registry→broker→gateway→downstream.

**Broker.** `POST /tasks {task_text, domain?, constraints?}` → retrieve candidates `GET /agents?domain=&health=up` (classify domain first if omitted) → **LLM router** builds compact prompt from candidate `AgentSkill`s and returns **structured** `{agent_id, skill_id, arguments}`, validated against the real candidate set (never trust hallucinated ids) → dispatch: A2A agent via a2a-sdk client **through gateway-a2a**, MCP tool via MCP client **through gateway-mcp** (never direct — governance must apply) → aggregate/stream result, persist task + trace. Reliability guards: enum-constrained ids, deterministic skill-tag fallback, next-best retry on downstream failure, fan-out cap.

## Repo structure (uv workspace monorepo)

```
ClaudeWork/
├── docker-compose.yml
├── pyproject.toml                 # uv workspace root
├── libs/fabric_common/            # shared installable lib
│   ├── governance/  (opa client, PEP middleware.py, rate limiter)
│   ├── auth/        (OIDC/JWT validation, token exchange)
│   ├── telemetry/   (OTel setup)
│   ├── models/      (AgentEntry, AuditRecord pydantic)
│   └── registry_client/
├── services/
│   ├── registry/    (app/: main.py, api/, db/, alembic/)
│   ├── gateway_mcp/ (app/: main.py, proxy.py, middleware.py)
│   ├── gateway_a2a/ (app/: main.py, handler.py, sse.py)
│   └── broker/      (app/: main.py, router.py, dispatch_a2a.py, dispatch_mcp.py, adk_agent.py)
├── policies/        (authz.rego, rate_limits.rego, data_class.rego)  # OPA bundle
├── deploy/          (otel-collector-config.yaml, keycloak dev realm)
└── tests/           (e2e/, contract/)
```

### Critical files
- `/Users/anirbanguha/ClaudeWork/docker-compose.yml`
- `/Users/anirbanguha/ClaudeWork/libs/fabric_common/governance/middleware.py` (shared PEP — build once, both gateways use it)
- `/Users/anirbanguha/ClaudeWork/services/gateway_mcp/app/proxy.py`
- `/Users/anirbanguha/ClaudeWork/services/gateway_a2a/app/handler.py`
- `/Users/anirbanguha/ClaudeWork/services/broker/app/router.py`

## Phased build plan

- **Phase 0 — Foundations (wk 1):** uv workspace, `fabric_common` skeleton (OTel + pydantic models + OPA client), Compose with postgres + otel-collector + OPA, base Dockerfile, plus an **echo MCP server** and **echo A2A agent** as test fixtures.
- **Phase 1 — Registry (wk 1-2):** data model + Alembic, register/discover endpoints, A2A card fetch+validate+JWS verify, MCP descriptor→`list_tools`→synthetic card, heartbeat/health. *Exit: register both fixtures, discover by skill/domain.*
- **Phase 2 — Gateways + Governance (wk 2-4):** gateway-mcp (FastMCP proxy+mount+middleware) and gateway-a2a (passthrough+SSE); wire shared OPA PEP + JWT validation + rate limiter + audit + traces; baseline Rego. *Exit: every fixture call is policy-checked, audited, traced; deny works.*
- **Phase 3 — Broker (wk 4-6):** ADK agent + registry candidate retrieval + LLM router (structured output) + dispatch through both gateways + task persistence + streaming + reliability guards. *Exit: `POST /tasks` routes an NL task to the right fixture through governance.*
- **Phase 4 — Hardening (wk 6+):** token-exchange/OBO auth propagation, OPA bundle hot-reload, e2e + contract tests, Traefik TLS ingress, README/runbook. *Visualizer UI deferred — OTel traces already flow, so Grafana/Tempo is a later drop-in.*

## Top risks / pitfalls

1. **A2A SDK churn** — pin exact versions; wrap SDK calls behind `fabric_common` adapters.
2. **Auth propagation** — decide token-exchange vs forward early; never blind-forward (audience mismatch).
3. **Streaming through the proxy** — proxy SSE (A2A) / Streamable HTTP (MCP) without buffering while still emitting per-event audit/trace; test backpressure + client cancel → A2A `cancel()`.
4. **LLM router reliability** — enum-constrain ids, validate every choice, tag-match fallback, next-best retry.
5. **FastMCP `list_tools` latency** (~300-400ms) — cache tool lists in registry/gateway.
6. **MCP v2 beta temptation** — stay on stable v1.x for the MVP.
7. **Policy as afterthought** — build the shared PEP middleware in Phase 2; both protocols share one governance core.
8. **Signed card trust** — verify JWS on registration; unsigned-card path is a rogue-agent injection vector.

## Verification

- **Per-phase smoke tests** using the echo fixtures (Phase 0 deliverable).
- **End-to-end (after Phase 3):** `POST /tasks` with a natural-language request → confirm the broker classifies the domain, LLM router selects the correct fixture, the call traverses the gateway (assert an `audit_log` row + a single OTel trace spanning broker→gateway→fixture), and the result returns. Repeat with a **policy-deny** case (assert 403 + audit record, no downstream call) and a **streaming** case (assert SSE events arrive incrementally and are individually audited).
- **Contract tests:** validate stored Agent Cards against the A2A schema; validate MCP synthetic cards expose the fixture's tools.
- **Run:** `docker compose up`, then drive via `curl`/httpx against `registry`, `gateway-mcp` `/mcp`, and `broker` `/tasks`.
