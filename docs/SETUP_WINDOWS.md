# Tech Stack & Windows Setup Guide

## Tech stack

| Area | Technology |
|---|---|
| **Language / runtime** | Python 3.12 (inside containers) |
| **Packaging / monorepo** | `uv` workspace, `hatchling` build backend |
| **Web framework** | FastAPI, Uvicorn, Starlette, `sse-starlette` (SSE streaming) |
| **Agent protocols** | A2A — `a2a-sdk` 1.1.x · MCP — `fastmcp` 3.x / `mcp` |
| **LLM** | Anthropic SDK (`anthropic`), model `claude-opus-4-8` (the broker's router + synthesis) |
| **Policy engine** | Open Policy Agent (OPA) 1.7.1, policies in Rego |
| **Database** | PostgreSQL 16 · SQLAlchemy 2.x (async) + `asyncpg` · Alembic (migration scaffold) |
| **Data models / config** | Pydantic v2, `pydantic-settings` |
| **Auth** | `PyJWT` (JWT validation + on-behalf-of token exchange) |
| **Observability** | OpenTelemetry (SDK, OTLP exporter, FastAPI/httpx instrumentation) + OTel Collector |
| **HTTP client** | `httpx` |
| **Chat frontend** | Slack — `slack-bolt` (AsyncApp, Socket Mode), `slack-sdk`, `aiohttp` |
| **Containerization** | Docker + Docker Compose (one shared image, `command` selects each service) |
| **Testing** | `pytest`, `pytest-asyncio` |

**Container images pulled:** `python:3.12-slim`, `postgres:16`, `openpolicyagent/opa:1.7.1`,
`otel/opentelemetry-collector-contrib`, `ghcr.io/astral-sh/uv` (build stage).

**External dependencies you supply:** an **Anthropic API key** (required for the broker),
and—optionally—**Slack tokens** (for the chatbot) and your **MuleSoft MCP endpoint URL**.

---

## Windows setup — step by step

Recommended path: **Docker Desktop on WSL2**, with the project inside the WSL2 Linux
filesystem (best performance, avoids Windows line-ending issues). All commands below are
for the **Ubuntu/WSL2 terminal** unless noted.

### 1. Install WSL2 (one-time)
Open **PowerShell as Administrator**:
```powershell
wsl --install
```
Reboot when prompted. This installs WSL2 + Ubuntu. Launch **Ubuntu** from the Start menu
and create a Linux username/password.

### 2. Install Docker Desktop
- Download **Docker Desktop for Windows**: <https://www.docker.com/products/docker-desktop/>
- During install, keep **"Use WSL 2 instead of Hyper-V"** checked.
- After install, open Docker Desktop → **Settings → Resources → WSL Integration** → enable
  integration with your **Ubuntu** distro. Apply & restart.
- Verify in the Ubuntu terminal:
  ```bash
  docker version
  docker compose version
  ```

### 3. Install Git (in WSL)
```bash
sudo apt update && sudo apt install -y git
```

### 4. Get the project onto the laptop
Pick whichever applies:

- **From a Git repo** (if you've pushed it somewhere):
  ```bash
  cd ~
  git clone <your-repo-url> ClaudeWork
  cd ClaudeWork
  ```
- **From a copy/zip** (the project isn't a git repo by default): copy the `ClaudeWork`
  folder into your WSL home, e.g. from Windows Explorer paste it under
  `\\wsl$\Ubuntu\home\<you>\ClaudeWork`, then:
  ```bash
  cd ~/ClaudeWork
  ```
  > Keep files in the **WSL filesystem** (`~/ClaudeWork`), not `C:\...`, for speed and to
  > avoid CRLF problems with `scripts/*.sh` and the Rego/YAML config.

### 5. Configure environment
```bash
cp .env.example .env
nano .env          # or: code .env  (opens VS Code if installed)
```
Set at minimum:
```
ANTHROPIC_API_KEY=sk-ant-...
```
Optional (for Slack chatbot — see docs/SLACK_SETUP.md):
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

### 6. Build and start the stack
```bash
docker compose up -d --build
```
First build pulls images + resolves Python deps (a few minutes). Then:
```bash
docker compose ps          # all services Up; postgres healthy
```

### 7. Verify it works
```bash
# health
curl http://localhost:8000/healthz      # registry
curl http://localhost:8020/healthz      # broker

# register your MuleSoft MCP API (example)
curl -X POST http://localhost:8000/agents -H "content-type: application/json" -d '{
  "kind":"mcp_server","name":"mule-products","domain":"products",
  "endpoint_url":"https://<your-app>.cloudhub.io/mcp","transport":"streamable-http"}'

# allow its tool in policy: edit policies/authz.rego -> add tool name to allowed_mcp_tools
# mount it (no restart) + run a task
curl -X POST http://localhost:8010/admin/reload
curl -X POST http://localhost:8020/tasks -H "content-type: application/json" -d '{
  "task_text":"give me the details for product id 1","domain":"products"}'
```

### 8. (Optional) Start the Slack bot
After putting the Slack tokens in `.env` (see `docs/SLACK_SETUP.md`):
```bash
docker compose up -d --build chatbot
docker compose logs -f chatbot     # expect "⚡️ Bolt app is running!"
```

### 9. Run the tests
```bash
./scripts/test.sh                  # 13 tests; needs the stack up + ANTHROPIC_API_KEY
```

### 10. Day-to-day
```bash
docker compose logs -f broker      # tail a service
docker compose restart broker      # restart one service
docker compose down                # stop (data persists in the pg volume)
docker compose down -v             # stop AND wipe the database
```

---

## Windows gotchas

| Issue | Fix |
|---|---|
| **PowerShell `curl` is an alias** for `Invoke-WebRequest` (different syntax). | Use the **WSL/Ubuntu terminal** for the `curl` commands, or call `curl.exe` explicitly in PowerShell. |
| **`./scripts/test.sh` fails with `\r` errors** | Keep the repo in the WSL filesystem; if cloned on Windows, run `git config --global core.autocrlf false` before cloning, or `sed -i 's/\r$//' scripts/*.sh`. |
| **Slow file access / rebuilds** | Put the project under `~/…` in WSL, not `C:\`. |
| **Ports already in use** (8000, 8010, 8011, 8020, 8181, 5432, 4317, 4318, 9001, 9002) | Stop the conflicting app or change the host port in `docker-compose.yml`. |
| **Docker can't reach a service on the Windows host** | Use `host.docker.internal` as the hostname (works on Docker Desktop). Remote/cloud URLs work as-is. |
| **Low memory** | Give Docker Desktop ≥4 GB (Settings → Resources). |

---

## Pure-Windows (no WSL) alternative
You can run everything from **PowerShell** with Docker Desktop's default backend, but:
use `curl.exe` (not `curl`), run `scripts/test.sh` via Git Bash, and expect slower bind-mount
performance. The WSL2 path above is strongly recommended.
