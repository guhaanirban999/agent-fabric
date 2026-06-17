# Open-Source Agent Fabric

An open-source replacement for **MuleSoft Agent Fabric** — the enterprise *agent control plane*.
It gives you a **Registry**, governed **Gateways** (the Omni-Gateway replacement), and a **Broker**
on top of the two open agent protocols: Google **A2A** (`a2a-sdk`) and Anthropic **MCP** (`mcp`/`fastmcp`).

> Your MuleSoft APIs are exposed as MCP servers. This project is the *fabric* that registers,
> governs, and orchestrates those tools and any A2A agents — no Omni Gateway license required.

## Pillars (MVP scope)

| Pillar | Service | Status |
|---|---|---|
| Agent Registry | `services/registry` | Phase 1 |
| Agent Gateway + Governance (Omni-Gateway replacement) | `services/gateway_mcp`, `services/gateway_a2a` | Phase 2 |
| Agent Broker | `services/broker` | Phase 3 |
| Agent Visualizer | *(deferred — OTel traces already emitted, drop in Grafana/Tempo later)* | — |

## Layout

```
libs/fabric_common      shared lib: models, telemetry, governance (OPA), auth, registry client
services/               registry, gateway_mcp, gateway_a2a, broker
fixtures/               echo_mcp + echo_a2a  (test agents/tools to exercise the fabric)
policies/               OPA Rego bundle (authz, rate limits, data classification)
deploy/                 otel-collector config, keycloak dev realm
docker-compose.yml      single-host stack
```

## Prerequisites

Everything runs in containers, so you only need:

- **Docker Desktop** (or Docker Engine) with the Compose plugin — `docker compose version`
- An LLM API key for the broker (Phase 3) — set `ANTHROPIC_API_KEY` in `.env`

You do **not** need Python or `uv` on the host; service images use Python 3.12 + `uv`.
(For local linting/editing only, install Python ≥3.11 and `uv`.)

## Quick start (Phase 0)

```bash
cp .env.example .env
docker compose up --build
```

This starts the infrastructure and the two fixtures:

| Service | URL | Purpose |
|---|---|---|
| postgres | `localhost:5432` | registry / audit store |
| opa | `localhost:8181` | policy decision point |
| otel-collector | `localhost:4317/4318` | traces + metrics (logged to console for now) |
| echo-mcp | `localhost:9001/mcp` | sample MCP tool server |
| echo-a2a | `localhost:9002/` | sample A2A agent (card at `/.well-known/agent-card.json`) |

Smoke-test the fixtures:

```bash
# A2A agent card (plain HTTP GET works)
curl -s localhost:9002/.well-known/agent-card.json | jq
```

The MCP server uses **Streamable HTTP**, which requires an `initialize` handshake +
session id before `tools/list` — a raw curl returns `Missing session ID`. Use a real
MCP client instead:

```bash
docker compose run --rm --no-deps echo-mcp python -c "
import asyncio
from fastmcp import Client
async def main():
    async with Client('http://echo-mcp:9001/mcp') as c:
        print('tools:', [t.name for t in await c.list_tools()])
        print('reverse(fabric):', (await c.call_tool('reverse', {'text':'fabric'})).data)
asyncio.run(main())
"
```

## Registry (Phase 1)

The registry catalogs A2A agents and MCP servers behind one descriptor (the A2A Agent
Card; MCP tools are synthesized into skills) and probes their health.

```bash
# Register the MCP fixture (registry introspects tools/list -> synthetic card)
curl -s -X POST localhost:8000/agents -H 'content-type: application/json' -d '{
  "kind":"mcp_server","name":"echo-mcp","domain":"demo",
  "endpoint_url":"http://echo-mcp:9001/mcp","transport":"streamable-http"}'

# Register the A2A fixture (registry fetches /.well-known/agent-card.json)
curl -s -X POST localhost:8000/agents -H 'content-type: application/json' -d '{
  "kind":"a2a_agent","domain":"demo","card_url":"http://echo-a2a:9002"}'

# Discover
curl -s 'localhost:8000/agents?skill=reverse'      # -> echo-mcp
curl -s 'localhost:8000/agents?domain=demo&health=up'
curl -s  localhost:8000/domains                     # -> ["demo"]
```

Endpoints: `POST /agents`, `GET /agents` (filters: `domain,kind,skill,tag,health`),
`GET /agents/{id}`, `GET /agents/{id}/card`, `POST /agents/{id}/heartbeat`,
`DELETE /agents/{id}`, `GET /domains`. Schema is created at startup for dev; the
production path is `alembic upgrade head` (see `services/registry/alembic`).

## Gateways + Governance (Phase 2)

The **Omni-Gateway replacement**. Both gateways share one enforcement core
(`fabric_common.governance`): OPA decision → policy-driven rate limit → audit row +
OTel span, fail-closed. Policy lives in `policies/authz.rego`.

- **gateway-mcp** (host `8010`) — a FastMCP composite that proxies every registered
  MCP server behind `/mcp`. Middleware enforces policy on `tools/call` and filters
  `tools/list` per subject (a tool the policy denies is invisible *and* uncallable).
- **gateway-a2a** (host `8011`) — a JSON-RPC + SSE reverse proxy to registered A2A
  agents at `POST /a2a/{agent_id}`, enforcing policy before forwarding.

```bash
# MCP: list is filtered, allowed call works, ungoverned tool is denied
docker compose run --rm --no-deps gateway-mcp python -c "
import asyncio; from fastmcp import Client
async def m():
    async with Client('http://gateway-mcp:8000/mcp') as c:
        print('visible tools:', [t.name for t in await c.list_tools()])
        print((await c.call_tool('reverse', {'text':'governed'})).data)
asyncio.run(m())"

# A2A: a governed SendMessage (note the required A2A-Version header)
AID=$(curl -s 'localhost:8000/agents?kind=a2a_agent' | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["id"])')
curl -s -X POST localhost:8011/a2a/$AID -H 'A2A-Version: 1.0' -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"hi"}],"messageId":"m1"}}}'

# Inspect the audit trail (per gateway)
curl -s 'localhost:8010/audit?limit=5'   # MCP
curl -s 'localhost:8011/audit?limit=5'   # A2A
```

Governance is fail-closed: if OPA is unreachable, calls are denied (and audited as
`opa-unreachable-fail-closed`).

## Broker (Phase 3)

The orchestration pillar (host `8020`). Takes a natural-language task, retrieves
healthy candidates from the registry, routes with an LLM (Anthropic SDK, **forced
tool-use** so output is structured and every `agent_id` is validated against the real
candidate set), then dispatches **through the governed gateways** (never directly) and
persists the task. Tries routes best-first with next-best retry.

```bash
# Requires ANTHROPIC_API_KEY in .env (see below). Then:
curl -s -X POST localhost:8020/tasks -H 'content-type: application/json' \
  -d '{"task_text":"reverse the word fabric"}'
# -> routes to echo-mcp/reverse {text:"fabric"} -> result "cirbaf", state "completed"

curl -s -X POST localhost:8020/tasks -H 'content-type: application/json' \
  -d '{"task_text":"add 17 and 25"}'              # -> echo-mcp/add -> 42.0

curl -s 'localhost:8020/tasks?limit=5'            # recent tasks (persisted)
curl -s  localhost:8020/tasks/<task_id>           # one task record
```

Optionally scope routing with `{"task_text":"...","domain":"demo"}`. The router is an
isolated module (`broker_svc/router.py`) so Google ADK / LangGraph can replace it
without touching dispatch or persistence.

### Configuring `ANTHROPIC_API_KEY`

Get a key at [console.anthropic.com](https://console.anthropic.com), put it in `.env`
(already gitignored), then recreate the broker:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env   # or edit the existing empty line
docker compose up -d broker
```

Override the router model with `BROKER_MODEL` (default `claude-opus-4-8`).

## Slack chat frontend

A thin Slack bot (`services/chatbot`, Socket Mode — no public URL) bridges Slack to the
broker's conversational endpoint `POST /chat`. The broker adds **multi-turn memory**
(`ConversationStore`), **auto-routing** (all healthy candidates), and an **LLM synthesis**
step that turns tool results into friendly replies. Smalltalk gets a conversational answer
with no tool call. Dispatch still goes through the governed gateways.

```bash
# Test the conversational endpoint directly (no Slack needed):
curl -s -X POST localhost:8020/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo","message":"give me the details for product id 1"}'
# follow-up in the same session resolves via memory:
curl -s -X POST localhost:8020/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo","message":"what about #5?"}'
```

**Slack setup** (one-time): create an app from the manifest in `docs/SLACK_SETUP.md`,
enable Socket Mode, and put the two tokens (`SLACK_BOT_TOKEN=xoxb-…`,
`SLACK_APP_TOKEN=xapp-…`) in `.env`, then `docker compose up -d chatbot`. Mention the bot
or DM it.

## Hardening (Phase 4)

- **Tests** — contract + e2e + unit suite. Stack must be up:
  ```bash
  ./scripts/test.sh           # 13 tests: registry contracts, gateway governance,
                              # broker routing, SSE streaming, OBO token helper
  ```
- **OPA hot-reload** — OPA runs with `--watch`; edits to `policies/authz.rego` apply with
  no restart.
- **Dynamic gateway refresh** — `POST localhost:8010/admin/reload` mounts newly-registered
  MCP servers without a restart.
- **SSE streaming** — `POST /tasks/stream` emits `accepted → candidates → routed →
  dispatching → completed` events.
- **Token-exchange (OBO)** — `fabric_common.auth.mint_downstream_token()` mints a
  short-lived on-behalf-of token (original `sub` + fabric `act`) for downstream auth
  propagation (helper + tests ready; gateway wiring is the last step once an IdP is set).

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for operations and the remaining production items
(full OBO wiring, Traefik TLS ingress, Redis-backed rate limiting, Alembic migrations).

## Roadmap

See [`docs/PLAN.md`](docs/PLAN.md) for the full phased build plan. All four phases
(registry, governed gateways, broker, hardening) are implemented and verified in Docker.
