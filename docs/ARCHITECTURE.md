# Agent Fabric — Layered Architecture (as built)

An open-source replacement for MuleSoft Agent Fabric: a chat frontend on top of an
LLM broker, governed gateways, and an agent/tool registry. All services run in one
Docker Compose stack and share the `fabric_common` library.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  L1  CHANNEL / FRONTEND                                                         │
│  ┌──────────────────────────┐     ┌───────────────────────────────────────┐    │
│  │ chatbot (Slack, Socket    │     │ Direct HTTP clients                   │    │
│  │ Mode, outbound WS, no port)│     │ curl / future web UI / API consumers  │    │
│  └────────────┬─────────────┘     └───────────────────┬───────────────────┘    │
└───────────────│───────────────────────────────────────│────────────────────────┘
                │ POST /chat {session_id,message}        │ POST /tasks, /tasks/stream
                ▼                                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  L2  CONVERSATION & ORCHESTRATION   — broker  (:8020)                           │
│   /chat  → ChatOrchestrator:  load memory → route(history) → dispatch →          │
│            synthesize NL reply → persist turn                                    │
│   /tasks → Orchestrator:       route → dispatch (best-first, next-best retry)    │
│   ┌─────────────┐  ┌──────────────────────┐  ┌──────────────────────────────┐   │
│   │ Router      │  │ ConversationStore     │  │ Synthesis (Anthropic)         │   │
│   │ Anthropic   │  │ multi-turn memory     │  │ tool result → friendly reply  │   │
│   │ forced tool │  └──────────────────────┘  └──────────────────────────────┘   │
│   │ use, schema │   (auto-route: all healthy candidates; smalltalk → no tool)   │
│   │ -aware      │                                                                │
│   └─────────────┘   dispatch ALWAYS via the gateways (no direct path)            │
└───────────────│──────────────────────────────────────────────────────│─────────┘
   reads catalog │ GET /agents?health=up                  dispatch       │
                ▼                                                         ▼
┌──────────────────────────────┐   ┌───────────────────────────────────────────────┐
│ L4 CONTROL PLANE / REGISTRY  │   │ L3  GOVERNANCE / GATEWAYS (Omni-Gateway repl.)  │
│ registry (:8000)             │   │  gateway-mcp (:8010)     gateway-a2a (:8011)    │
│  • catalog (agents/MCP/A2A)  │   │  FastMCP composite       JSON-RPC + SSE proxy   │
│  • discovery (domain/skill/  │   │  proxy + mount           to A2A agents          │
│    kind/health)              │   │        │                        │              │
│  • health prober             │   │        └────────┬───────────────┘              │
│  • A2A card fetch + JWS      │   │     shared PEP (fabric_common.governance):      │
│  • MCP introspection →       │   │     OPA decision → rate limit → audit → trace   │
│    synthetic card + schema   │   │                    │ decision query              │
└──────────────┬───────────────┘   │                    ▼                            │
               │ register/heartbeat │            ┌───────────────┐                   │
               │                    │            │ OPA (:8181)    │ policies/         │
               │                    │            │ PDP, --watch   │ authz.rego        │
               │                    └────────────┴───────┬───────┘                   │
               │                                  governed │ call                     │
               ▼                                          ▼                          │
┌──────────────────────────────────────────────────────────────────────────────┐  │
│  L5  PROTOCOLS / AGENTS & TOOLS                                                 │  │
│   MCP servers (MuleSoft APIs)              A2A agents                           │  │
│   • mule-products (CloudHub)               • echo-a2a (fixture)                 │  │
│   • echo-mcp (fixture)                     • (your A2A agents)                  │  │
└──────────────────────────────────────────────────────────────────────────────┘  │
                                                                                    │
┌───────────────────────── CROSS-CUTTING (all layers) ─────────────────────────────┘
│  DATA   Postgres (:5432): agent_entries · audit_log · broker_tasks · broker_conversations
│  OBSERVABILITY  OpenTelemetry Collector (:4317/4318); one trace id spans L2→L3→L5; audit_log
│  SHARED LIB  fabric_common: models · governance(OPA/PEP/ratelimit/audit) · auth(JWT/OBO) ·
│              telemetry · registry_client · config
└────────────────────────────────────────────────────────────────────────────────
```

## Layers at a glance

| Layer | Component(s) | Port | Responsibility |
|---|---|---|---|
| **L1 Channel** | `chatbot` (Slack Socket Mode) | none (outbound WS) | Thin bridge: relay user message → `/chat`, post reply in-thread. Self-loop guard + event dedupe. |
| | Direct clients (curl / UI) | — | Hit broker `/tasks`, `/tasks/stream`, `/chat` directly. |
| **L2 Orchestration** | `broker` | 8020 | Conversation memory, LLM routing, governed dispatch, NL synthesis, persistence. |
| **L3 Governance** | `gateway-mcp` | 8010 | Proxy+aggregate MCP servers behind one governed `/mcp`; policy on `tools/call`, per-subject `tools/list` filter. |
| | `gateway-a2a` | 8011 | Proxy A2A JSON-RPC/SSE; policy on `message_send`. |
| | `opa` (PDP) | 8181 | Evaluate `policies/authz.rego`; hot-reload (`--watch`); fail-closed. |
| **L4 Control plane** | `registry` | 8000 | Catalog + discovery + health; A2A card fetch/JWS; MCP introspection→synthetic card (incl. input schema). |
| **L5 Protocols** | MCP servers / A2A agents | (own) | The actual tools/agents — e.g. MuleSoft APIs exposed as MCP. |
| **Data** | `postgres` | 5432 | `agent_entries`, `audit_log`, `broker_tasks`, `broker_conversations`. |
| **Observability** | `otel-collector` | 4317/4318 | Traces + metrics; single trace id across the call chain; audit rows carry it. |
| **Shared** | `fabric_common` | — | Models, governance (OPA client/PEP/rate limit/audit), auth (JWT + OBO token-exchange), telemetry, registry client, config. |

## Request flow — a Slack message to a MuleSoft API

```
1. Slack message  ──(Socket Mode)──▶  chatbot
2. chatbot         ──POST /chat {session_id, message}──▶  broker
3. broker (ChatOrchestrator):
     a. ConversationStore.history(session_id)         ← multi-turn memory
     b. registry GET /agents?health=up                ← candidates (auto-route)
     c. Router (LLM, tool_choice=auto, schema-aware)  ← {agent_id, skill_id, args} or none
     d. dispatch_best_first ──▶ gateway-mcp /mcp
4. gateway-mcp (shared PEP):
     OPA decision (allow?) → rate limit → audit_log row → OTel span
     └─(allow)─▶ proxy to MuleSoft MCP server  ──▶  product JSON
5. broker: Synthesis LLM turns the result + history into a friendly reply
6. broker: persist user+assistant turns; return {reply, used_tool, trace_id}
7. chatbot: post reply in the Slack thread
```
Smalltalk short-circuits at 3c (no route → skip 3d/4, synthesize a conversational reply).
The same trace id flows broker → gateway → tool and is stamped on the `audit_log` row.

## Mapping to MuleSoft Agent Fabric pillars

| MuleSoft pillar | Here |
|---|---|
| Agent Registry | **L4** `registry` |
| Agent Broker | **L2** `broker` (+ LLM Router) |
| Agent Governance (Omni Gateway) | **L3** `gateway-mcp` / `gateway-a2a` + `opa` + shared PEP |
| Agent Visualizer | *(deferred)* — OTel traces already emitted at **Observability**; drop in Grafana/Tempo |
| *(net-new)* Chat frontend | **L1** Slack bot + broker `/chat` (memory + synthesis) |

## Key design properties

- **Single governed path:** the broker only dispatches through L3 gateways (`dispatch.py`), so every tool/agent call is policy-checked + audited. No bypass.
- **Thin frontend:** L1 has no business logic; memory + intelligence live in L2 so any future channel benefits.
- **Stateless services, state in Postgres:** registry/audit/tasks/conversations all persist; services can restart freely.
- **Fail-closed governance:** OPA unreachable ⇒ deny (audited as `opa-unreachable-fail-closed`).
- **Hot-reloadable policy + dynamic backends:** OPA `--watch`; `POST :8010/admin/reload` mounts newly-registered MCP servers without a restart.
