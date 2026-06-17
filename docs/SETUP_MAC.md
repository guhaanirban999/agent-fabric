# macOS Setup Guide

Set up and run the Agent Fabric on a Mac from the GitHub repo. Everything runs in
Docker, so the only real requirement is Docker Desktop — works on both **Apple Silicon
(M-series)** and **Intel** Macs.

## Software requirements

| Software | Why | How to get it |
|---|---|---|
| **macOS 12 (Monterey) or newer** | Docker Desktop support | — |
| **Docker Desktop for Mac** (incl. Compose) | runs the whole stack | brew cask or download (below) |
| **Git** | clone the repo | Xcode CLT or Homebrew |
| **An Anthropic API key** | the broker's LLM routing + reply synthesis | <https://console.anthropic.com> |
| Homebrew *(recommended)* | installs the above easily | <https://brew.sh> |
| `jq` *(optional)* | pretty-print JSON in the examples | `brew install jq` |
| `curl` | API calls | built into macOS |
| *(optional)* Slack tokens | only if you run the chat bot | see `docs/SLACK_SETUP.md` |

**Resources:** give Docker ≥4 GB RAM (Docker Desktop → Settings → Resources). Images
total ~2–3 GB on first pull.

**Apple Silicon note:** most images are multi-arch and run natively; the pinned
`openpolicyagent/opa:1.7.1` image is `amd64` and runs via emulation (works fine). For best
results enable **Settings → General → "Use Rosetta for x86/amd64 emulation"** in Docker
Desktop (Apple Silicon only).

---

## Setup — step by step

### 1. Install Homebrew (skip if you already have it)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
Follow the post-install hint to add `brew` to your PATH (Apple Silicon: `eval "$(/opt/homebrew/bin/brew shellenv)"`).

### 2. Install Docker Desktop
```bash
brew install --cask docker
```
Then **launch Docker Desktop** (from Applications) once and let it finish starting — the
whale icon in the menu bar should say "Docker Desktop is running." (Or download the
installer from <https://www.docker.com/products/docker-desktop/>.)

Verify in Terminal:
```bash
docker version
docker compose version
```

### 3. Install Git (if needed)
```bash
git --version           # if this prompts to install Xcode CLT, accept it
# or:
brew install git
```

### 4. Clone the repo
```bash
cd ~
git clone https://github.com/guhaanirban999/agent-fabric.git
cd agent-fabric
```

### 5. Configure environment
```bash
cp .env.example .env
open -e .env            # or: nano .env  /  code .env
```
Set at minimum:
```
ANTHROPIC_API_KEY=sk-ant-...
```
Optional (Slack bot — see `docs/SLACK_SETUP.md`):
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

### 6. Build and start the stack
```bash
docker compose up -d --build
```
First run pulls images + resolves Python deps (a few minutes). Then:
```bash
docker compose ps        # all services Up; postgres healthy
```

### 7. Verify it works
```bash
# health
curl http://localhost:8000/healthz      # registry
curl http://localhost:8020/healthz      # broker

# register a MuleSoft MCP API (example) and route a task
curl -X POST http://localhost:8000/agents -H "content-type: application/json" -d '{
  "kind":"mcp_server","name":"mule-products","domain":"products",
  "endpoint_url":"https://<your-app>.cloudhub.io/mcp","transport":"streamable-http"}'

# allow its tool: edit policies/authz.rego -> add the tool name to allowed_mcp_tools
curl -X POST http://localhost:8010/admin/reload     # mount it (no restart)

curl -X POST http://localhost:8020/tasks -H "content-type: application/json" -d '{
  "task_text":"give me the details for product id 1","domain":"products"}'
```

### 8. (Optional) Start the Slack bot
After adding the Slack tokens to `.env`:
```bash
docker compose up -d --build chatbot
docker compose logs -f chatbot     # expect "⚡️ Bolt app is running!"
```

### 9. Run the tests
```bash
./scripts/test.sh                  # 13 tests; needs the stack up + ANTHROPIC_API_KEY
```

### 10. Day-to-day commands
```bash
docker compose logs -f broker      # tail a service
docker compose restart broker      # restart one service
git pull                           # get updates, then:
docker compose up -d --build       # rebuild changed services
docker compose down                # stop (database persists)
docker compose down -v             # stop AND wipe the database volume
```

---

## macOS gotchas

| Issue | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running — launch it and wait for the whale icon. |
| **Port 5432 in use** | A local Postgres/Postgres.app is running. Stop it, or remap the host port in `docker-compose.yml`. Other ports: 8000/8010/8011/8020/8181/4317/4318/9001/9002. |
| Apple Silicon platform warning on OPA | Harmless (emulated). Enable Rosetta emulation in Docker Desktop settings to silence/speed it. |
| Slow file access on big rebuilds | Docker Desktop → Settings → General → file sharing implementation = **VirtioFS**. |
| GitHub auth for `git push` | Handled by the macOS Keychain credential helper after your first authenticated push. |
| `jq: command not found` in examples | `brew install jq`, or drop the `| jq` and read raw JSON. |

---

## Updating to a new version
```bash
cd ~/agent-fabric
git pull                       # or: git checkout v0.1.0  for a specific release
docker compose up -d --build
```

See also: `README.md` (usage), `docs/ARCHITECTURE.md` (layers), `docs/RUNBOOK.md`
(operations), `docs/SLACK_SETUP.md` (chat bot).
