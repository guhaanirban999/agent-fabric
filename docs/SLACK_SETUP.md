# Slack Bot Setup — Agent Fabric

The chatbot uses **Socket Mode**, so it needs **no public URL** — it runs fine from
Docker on your laptop. You need two tokens.

## 1. Create the app from a manifest

Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest** →
pick your workspace → paste:

```yaml
display_information:
  name: Agent Fabric Bot
features:
  bot_user:
    display_name: agent-fabric
    always_online: true
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - chat:write
      - im:history
      - im:read
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
  socket_mode_enabled: true
  org_deploy_enabled: false
  token_rotation_enabled: false
```

## 2. Get the two tokens

- **App-Level Token** (`xapp-…`): *Settings → Basic Information → App-Level Tokens →
  Generate* with scope **`connections:write`**. (Socket Mode uses this to open the
  WebSocket.) → `SLACK_APP_TOKEN`.
- **Bot Token** (`xoxb-…`): *Settings → Install App → Install to Workspace* → authorize →
  copy the **Bot User OAuth Token**. → `SLACK_BOT_TOKEN`.

> Which goes where: `xapp-` → the Socket Mode handler; `xoxb-` → the bot client. Mixing
> these up is the #1 setup mistake.

## 3. Confirm DMs are enabled

*App Home → Show Tabs → Messages Tab* must be **on**, with "Allow users to send Slash
commands and messages from the messaging tab" checked — otherwise DMs never reach the bot.

## 4. Configure and run

Put both tokens in `.env` (alongside `ANTHROPIC_API_KEY`):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

```bash
docker compose up -d --build chatbot
docker compose logs -f chatbot          # should show a Socket Mode connection
```

## 5. Use it

- **Channel:** invite the bot (`/invite @agent-fabric`), then
  `@agent-fabric give me the details for product 1` → threaded reply.
- **DM:** open a direct message with the bot and just type.
- **Memory:** ask a follow-up in the same thread ("what about #5?").
- **Smalltalk:** "hi" → conversational reply, no tool call.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bot never responds in channel | Invite it to the channel; ensure `app_mention` event + `app_mentions:read` scope; reinstall after scope changes. |
| DMs ignored | Messages tab not enabled, or missing `im:history`. |
| `not_authed` / `invalid_auth` in logs | Wrong token in wrong slot (`xapp-` vs `xoxb-`). |
| Replies duplicated | (Shouldn't happen — `event_id` dedupe handles Slack retries.) |
| 503 from broker | `ANTHROPIC_API_KEY` not set. |
