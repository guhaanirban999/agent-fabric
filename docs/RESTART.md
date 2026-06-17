# Restart Guide — stopping & resuming the demo

The demo state **persists** across a machine shutdown:

- **Registered agents, audit log, tasks, chat memory** → in the Postgres `pgdata` volume.
- **API keys / Slack tokens** → in `.env` on disk.
- **Policy** (`policies/authz.rego`) → on disk.

So after a reboot you **don't** need to re-register your MuleSoft API, re-edit the policy,
or re-enter keys. The only thing to know: the infrastructure containers (postgres, opa,
otel-collector) don't auto-start, so resume with `docker compose up -d`.

## When switching off

Just shut down the machine — or stop cleanly first (the database is kept either way):

```bash
docker compose stop          # or: docker compose down
```

> ⚠️ **Never** run `docker compose down -v` unless you intend to wipe the database — it
> deletes the `pgdata` volume (registered agents, audit, tasks, chat memory all lost).

## When starting the demo again

```bash
# 1. Power on. Start Docker Desktop; wait until the menu-bar whale says
#    "Docker Desktop is running".

# 2. Go to the project
cd ~/agent-fabric                  # wherever you keep it

# 3. Start everything (no --build needed unless you changed code)
docker compose up -d

# 4. Wait ~30s, then confirm all services are Up and postgres is healthy
docker compose ps

# 5. Verify the demo state survived
curl -s 'localhost:8000/agents?domain=products'   # mule-products should be listed
#    health may read "unknown" for up to ~30s, then the prober flips it to "up"

# 6. Run the demo
curl -s -X POST localhost:8020/tasks -H 'content-type: application/json' \
  -d '{"task_text":"give me the details for product id 1","domain":"products"}'
#    (or just message the Slack bot — it auto-reconnects via Socket Mode)
```

### If a tool call is denied / not found right after startup

Rare — the MCP gateway re-mounts registered MCP servers at startup (with retries) — but if
needed, nudge it:

```bash
curl -X POST localhost:8010/admin/reload    # re-mount registered MCP backends
```

## What persists vs. what resets

| Survives reboot | Resets on restart (rebuilt automatically) |
|---|---|
| Registered agents, audit log, tasks, chat memory (Postgres volume) | In-memory rate-limit buckets |
| `.env` (API keys, Slack tokens) | Gateway's mounted-backend list (re-loaded from the registry) |
| `policies/authz.rego` edits | Slack event-dedupe cache |

None of the "resets" affect demo continuity.

## TL;DR

```
Start Docker Desktop  →  docker compose up -d  →  demo
```
