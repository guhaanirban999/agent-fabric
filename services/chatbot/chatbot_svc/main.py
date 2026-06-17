"""Slack Socket Mode bridge to the Agent Fabric broker.

Thin by design: it relays a user's message to the broker's /chat (which does routing,
governance, memory, and synthesis) and posts the reply back in-thread. No business logic.

Socket Mode = outbound WebSocket only, so no public URL/port is needed.
"""

from __future__ import annotations

import asyncio
import logging
import re

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp

from fabric_common.config import get_settings
from chatbot_svc.broker_client import BrokerClient
from chatbot_svc.dedupe import TTLDedupe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot")

settings = get_settings()
app = AsyncApp(token=settings.slack_bot_token)
broker = BrokerClient(settings.broker_url)
seen = TTLDedupe(ttl=300)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _session_id(team: str, event: dict) -> str:
    """One conversation per DM (by user) or per channel thread (by thread root)."""
    if event.get("channel_type") == "im":
        return f"{team}:dm:{event.get('user')}"
    thread = event.get("thread_ts") or event.get("ts")
    return f"{team}:{event.get('channel')}:{thread}"


async def _handle(event: dict, body: dict, say) -> None:
    # Never react to our own / non-user messages (prevents self-reply loops).
    if event.get("bot_id") or event.get("subtype"):
        return
    # Drop Slack's event redeliveries.
    event_id = body.get("event_id")
    if event_id and not seen.add(event_id):
        return

    text = _MENTION_RE.sub("", event.get("text", "")).strip()
    if not text:
        return

    team = body.get("team_id") or event.get("team") or ""
    session_id = _session_id(team, event)
    reply_ts = event.get("thread_ts") or event.get("ts")

    try:
        reply = await broker.chat(session_id, text)
    except Exception:
        logger.exception("broker /chat failed")
        reply = "Sorry — I hit an error reaching the agent fabric. Please try again."

    await say(text=reply, thread_ts=reply_ts)


@app.event("app_mention")
async def on_mention(event, body, say) -> None:
    await _handle(event, body, say)


@app.event("message")
async def on_message(event, body, say) -> None:
    # Only handle direct messages here; channel posts come via app_mention.
    if event.get("channel_type") != "im":
        return
    await _handle(event, body, say)


async def main() -> None:
    if not settings.slack_bot_token or not settings.slack_app_token:
        raise SystemExit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    logger.info("starting Slack Socket Mode handler -> broker at %s", settings.broker_url)
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
