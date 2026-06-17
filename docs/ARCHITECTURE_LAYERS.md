# Layered Architecture (L1–L5) — workflow, tech stack & rationale

This document decomposes the Agent Fabric into five layers along the **request-flow axis**
(L1 = where humans/systems enter, L5 = where work is actually served & persisted). For each layer
it gives the **components**, the **technology stack**, **why that stack** was chosen, and the
**step-by-step workflow** through it. For the higher-level overview and the mapping to MuleSoft
Agent Fabric, see [ARCHITECTURE.md](ARCHITECTURE.md).

```
        ┌───────────────────────────────────────────────────────────┐
  L1    │ EXPERIENCE     Onboarding Console UI · Slack chatbot · curl │  how you talk to the fabric
        ├───────────────────────────────────────────────────────────┤
  L2    │ ORCHESTRATION  Broker (LLM router · dispatch · chat memory) │  intent → governed action
        ├───────────────────────────────────────────────────────────┤
  L3    │ GOVERNANCE     gateway-mcp · gateway-a2a · PEP · OPA (PDP)  │  policy + rate-limit + audit
        ├───────────────────────────────────────────────────────────┤
  L4    │ CONTROL PLANE  Registry · health prober · card introspection│  source of truth + discovery
        ├───────────────────────────────────────────────────────────┤
  L5    │ DATA & BACKENDS Postgres · MCP servers · A2A agents         │  persistence + real capability
        └───────────────────────────────────────────────────────────┘
   Cross-cutting (all layers): OpenTelemetry · OIDC/JWT + OBO · pydantic-settings config ·
                               uv-workspace monorepo baked into one Docker image
```

---

## L1 — Experience / Interface Layer
**Boundary:** every entry point a human or external system uses; no business logic beyond shaping
requests and rendering responses.

**Components**
- **Onboarding Console UI** (planned — `services/console`): web form to register MCP servers / A2A
  agents, list + live health, delete, and optionally flip the policy allow-list.
- **Slack chatbot** (`services/chatbot`): conversational entry to the broker.
- **Raw REST clients** (curl / Postman): the registry & broker HTTP APIs directly.

**Tech stack**
- Console: **FastAPI** as a Backend-for-Frontend + **server-rendered HTML with HTMX/vanilla JS**
  (no Node build step). Talks to L4/L3 over the compose network with **httpx**.
- Chatbot: **Python `slack-bolt` AsyncApp + AsyncSocketModeHandler** (Socket Mode = outbound
  WebSocket, no public URL/port).

**Why this stack**
- A FastAPI BFF keeps the browser **same-origin** (no CORS sprawl) and lets the server own the
  multi-step onboarding choreography (register → reload → poll). HTMX over React avoids a node
  toolchain so the UI ships inside the **same fat Python image** as everything else.
- Socket Mode means the demo needs **no inbound ingress** to get a chat surface — critical for a
  laptop/single-host demo.

**Workflow (Console onboarding)**
1. Operator opens the console, fills the form (MCP: `name`, `endpoint_url`, `domain`, `tags`,
   `auth`; A2A: `card_url`, `domain`, `tags`).
2. Console BFF `POST`s to the **registry** `POST /agents`; surfaces 400 (validation) / 502
   (unreachable/introspection) inline.
3. For an MCP server, the BFF then calls **gateway-mcp** `POST /admin/reload` to mount it.
4. BFF (optionally) updates the **policy allow-list** so the new tool/skill is callable.
5. BFF polls `GET /agents/{id}` until `health: up`; the page shows the live status table.

---

## L2 — Orchestration / Brokerage Layer
**Boundary:** turns a natural-language intent into a concrete, governed downstream call; owns
routing, retries, conversation memory — but never bypasses L3.

**Components** (`services/broker`): LLM **router** (`router.py`), **dispatch** (`dispatch.py`,
best-first with next-best retry), **chat orchestrator** + **conversation store** (asyncpg), sync
`POST /tasks` and streaming `POST /tasks/stream`, plus `POST /chat`.

**Tech stack**
- **FastAPI** + **uvicorn**.
- **Anthropic SDK** with **forced tool-use** (`select_routes`, `agent_id` enum-constrained to real
  candidate ids) — model via `BROKER_MODEL` (default `claude-opus-4-8`).
- **sse-starlette** for streamed progress; **fastmcp client** (MCP dispatch) and **httpx**
  (A2A JSON-RPC) — but always pointed at the **gateways**, never the agents directly.
- **asyncpg** for `broker_tasks` / `broker_conversations`.

**Why this stack**
- Routing across heterogeneous agents is a reasoning problem → a **capable LLM** (Anthropic Opus).
  Forced tool-use with an enum constraint makes the route **machine-valid and hallucination-proof**.
- The router is a **swappable module** (Anthropic chosen over Google ADK because ADK is
  Gemini/Vertex-oriented and litellm-wrapping Anthropic added fragility for no gain).
- SSE gives real-time `accepted → candidates → routed → dispatching → completed` UX without
  websockets.

**Workflow**
1. Receive NL task (`/tasks`) or chat turn (`/chat`).
2. Query **registry** for healthy candidates (optionally domain-scoped); attach each tool/skill's
   `input_schema` so the LLM emits exact arg names/types.
3. LLM returns a validated `RouteDecision` (agent, skill, arguments, confidence).
4. **Dispatch best-first through L3 gateways**; on failure, retry the next-best candidate.
5. Persist the task; for chat, synthesize a natural-language reply and store conversation memory.

---

## L3 — Governance / Gateway Layer  (the Omni-Gateway replacement)
**Boundary:** the single **Policy Enforcement Point (PEP)** every agent/tool call must pass
through; decides allow/deny, rate-limits, redacts, and audits. Fail-closed.

**Components**
- **gateway-mcp** (`/mcp`, `POST /admin/reload`, `GET /audit`).
- **gateway-a2a** (`POST /a2a/{id}`, `GET /audit`).
- Shared **PEP** in `libs/fabric_common/governance` (OPA call + token-bucket rate limit + audit
  sink + OTel span).
- **OPA** as the external **Policy Decision Point (PDP)**.

**Tech stack**
- gateway-mcp: **FastMCP composite proxy** (`create_proxy` + `mount`) with middleware enforcing
  `on_call_tool` (deny → ToolError) and per-subject `on_list_tools` filtering.
- gateway-a2a: **FastAPI** JSON-RPC/SSE **reverse proxy** (resolves the agent from the registry
  per-request — so A2A needs **no reload**, unlike MCP).
- PEP: shared Python lib; **asyncpg** audit sink to a shared `audit_log` table.
- **Open Policy Agent `1.7.1`**, **Rego v1**, run with **`--watch`** over a bind-mounted
  `./policies` (hot-reload, no restart).

**Why this stack**
- **Externalizing policy into OPA/Rego** is the core "fabric" idea — governance changes without
  redeploying code, exactly what MuleSoft's Omni-Gateway / Agent Fabric provides commercially.
- FastMCP composition lets one endpoint **front many MCP backends** with uniform middleware.
- **Fail-closed** (OPA unreachable → deny + audit `opa-unreachable-fail-closed`) is the safe
  default for a control plane.

**Workflow**
1. Inbound call hits the gateway; build a `PolicyInput` (subject, protocol, action, tool/skill,
   domain, arg_keys, data_classes).
2. Query OPA `POST /v1/data/fabric/authz` → `PolicyDecision {allow, reason, rate_limit, redact_keys}`.
3. Enforce the token-bucket rate limit per `{subject}:{target}`.
4. Write an `AuditRecord` (with OTel trace id) to Postgres.
5. If allowed, proxy to the L5 backend and stream the result back; else return a policy-denied error.

---

## L4 — Control Plane / Registry Layer
**Boundary:** the system of record for "what agents/tools exist, how to reach them, and are they
healthy." No call passes through it at runtime — it is discovery + liveness.

**Components** (`services/registry`): register/discovery API, **health prober** (~30s), **card
introspection** (`cards.py`), SQLAlchemy ORM/repository.

**Tech stack**
- **FastAPI** + **SQLAlchemy async ORM** + **asyncpg**; **Alembic** scaffold (dev uses
  `create_all`, production path `alembic upgrade head`).
- **Pydantic** contracts shared via `fabric_common.models` (`RegisterRequest`, `AgentEntry`,
  `AgentSkillSummary`, enums).
- **fastmcp client** to introspect MCP `tools/list` (captures each tool's `inputSchema`); **a2a-sdk**
  to fetch + parse the A2A `/.well-known/agent-card.json` (+ optional JWS verify).

**Why this stack**
- A relational store + Pydantic contracts give a **typed, queryable catalog** that every other
  service imports — one schema, no drift.
- An **active prober** (not just self-heartbeat) means the broker only ever routes to **live**
  backends; capturing `inputSchema` at registration is what lets L2's LLM produce exact arguments.

**Workflow**
1. `POST /agents`: for MCP → introspect tools → synthesize card; for A2A → fetch & parse card
   (verify signature). Persist an `AgentEntry`.
2. Discovery: `GET /agents?domain=&kind=&skill=&tag=&health=`, `GET /agents/{id}`, `/domains`.
3. Prober loops every ~30s: MCP → `list_tools`, A2A → GET card; flips `health` up/down.
4. `DELETE /agents/{id}` for cleanup (re-registration currently creates a duplicate row — known gap).

---

## L5 — Data & Backends Layer
**Boundary:** durable state and the actual capabilities the fabric governs — the only layer that
"does real work" or stores bytes.

**Components**
- **Postgres**: `registry` rows, `audit_log`, `broker_tasks`, `broker_conversations`.
- **MCP servers**: `mule-products` (live MuleSoft CloudHub, real product API), `echo-mcp` fixture.
- **A2A agents**: `echo-a2a` fixture, `writer-a2a` (LLM writing assistant).

**Tech stack**
- **Postgres 16** (single durable volume `pgdata`).
- MCP fixtures: **FastMCP**. A2A fixtures: **a2a-sdk 1.x + FastAPI + uvicorn**; `writer-a2a` adds the
  **Anthropic SDK**. Real backend: **MuleSoft CloudHub** MCP server (remote, HTTP).

**Why this stack**
- One Postgres instance is the right durability story for a **single-host** deployment (catalog,
  audit, and task history survive restarts via the volume).
- Shipping **both an MCP and an A2A fixture** exercises both protocol paths; the **live MuleSoft**
  server proves the fabric governs real enterprise APIs, not just toys.

**Workflow**
1. Backends publish capability (MCP `tools/list`; A2A well-known card).
2. They are registered into L4, governed by L3, orchestrated by L2, reached from L1.
3. Postgres persists the catalog, every audited decision, and task/conversation history.

---

## Cross-cutting concerns (span all layers)
- **Telemetry:** OpenTelemetry spans; a single trace id flows broker → gateway → backend and lands
  in `audit_log.trace_id`.
- **Auth:** OIDC/JWT validation + on-behalf-of token-exchange helper (`fabric_common.auth`) — helper
  built & tested; full gateway wiring pending an IdP.
- **Config:** `pydantic-settings` (`fabric_common.config`), 12-factor env vars; service URLs
  (`registry_url`, `gateway_mcp_url`, …) are centralized for reuse.
- **Packaging:** uv-workspace monorepo, **one fat Docker image**; each service selects its entrypoint
  via the compose `command`.

---

## How the Onboarding UI (L1) ties the layers together
A single "Onboard" action choreographs L4 → L3 → (policy) → L4:
1. **L4 register** — `POST /agents` (MCP introspect / A2A card fetch).
2. **L3 mount** — `POST gateway-mcp/admin/reload` (MCP only; A2A resolves per-request).
3. **Policy** — make the tool/skill callable. The one step with **no HTTP API today** (the allow-list
   lives in `policies/authz.rego`). A clean fix is to move the allow-lists into a JSON data file OPA
   loads (`policies/data.json`, referenced from rego); a console could then write it and OPA `--watch`
   hot-reloads — turning onboarding into a true one-click flow.
4. **L4 health** — poll `GET /agents/{id}` until `up`; render the live table.
