"""
Slack Bolt entrypoint: app mentions → InterviewAgent.

Run with Socket Mode (recommended for local dev) or HTTP (Events API behind HTTPS).

Slack app checklist (otherwise the bot stays silent):
- Bot token + (Socket Mode: SLACK_APP_TOKEN) or (HTTP: SLACK_SIGNING_SECRET + public URL)
- **Process must be running** while you test (e.g. `python slack_bot.py`)
- Event Subscriptions: enable `app_mention` and **`message.im`** (DMs do not reliably emit `app_mention`)
- OAuth scopes include `app_mentions:read`, `chat:write`, **`im:history`**
- In a *channel* (not DM): `/invite @YourBot` so it can see mentions
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent import build_default_agent
from utils import strip_bot_mention

# Load .env from this file's directory (not cwd), so `python slack_bot.py` works from anywhere.
load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_agent = build_default_agent()


def _slack_thread_context(client, event: dict) -> str:
    """
    Text from earlier messages in the thread (or recent channel history if not threaded)
    so phrases like 'record this' can refer to the interview write-up above.
    """
    ch = event.get("channel")
    cur_ts = event.get("ts")
    if not ch or not cur_ts:
        return ""
    lines: list[str] = []
    try:
        thread_ts = event.get("thread_ts")
        if thread_ts:
            resp = client.conversations_replies(channel=ch, ts=thread_ts, limit=50)
            for m in resp.get("messages") or []:
                if m.get("ts") == cur_ts:
                    continue
                if m.get("bot_id"):
                    continue
                if m.get("subtype") in ("channel_join", "channel_leave", "channel_topic"):
                    continue
                txt = (m.get("text") or "").strip()
                if txt:
                    lines.append(txt)
        else:
            resp = client.conversations_history(
                channel=ch,
                latest=cur_ts,
                limit=12,
                inclusive=False,
            )
            for m in reversed(resp.get("messages") or []):
                if m.get("bot_id"):
                    continue
                if m.get("subtype"):
                    continue
                txt = (m.get("text") or "").strip()
                if txt:
                    lines.append(txt)
    except Exception as exc:
        logger.warning("Could not load Slack context: %s", exc)
        return ""
    out = "\n\n".join(lines)
    if len(out) > 14_000:
        return out[:14_000] + "\n\n_(truncated)_"
    return out


def _reply_text(event: dict, client, text: str) -> None:
    channel = event["channel"]
    ts = event.get("thread_ts") or event["ts"]
    # Slack chat.postMessage text must stay under ~40k; leave margin for mrkdwn
    max_len = 39_000
    if len(text) > max_len:
        text = text[: max_len - 50] + "\n\n_(truncated for Slack length limits)_"
    client.chat_postMessage(
        channel=channel,
        thread_ts=ts,
        text=text,
        mrkdwn=True,
    )


def _process_user_text(event: dict, client, raw_text: str, logger) -> None:
    """Shared handler for app mentions and DM messages."""
    try:
        cleaned = strip_bot_mention(raw_text)
        if not cleaned:
            _reply_text(
                event,
                client,
                "Say something after the mention, or type a command. Try `help`.",
            )
            return
        # Only explicit help keywords should trigger the help menu.
        # For informal greetings ("hey", "hi", etc.), let the agent respond conversationally.
        if cleaned.lower() in ("help", "?", "commands", "usage"):
            out = _agent.handle_message("help")
        else:
            ctx = _slack_thread_context(client, event)
            out = _agent.handle_message(cleaned, ctx)
        _reply_text(event, client, out)
    except Exception as exc:  # noqa: BLE001
        logger.exception("handler failed")
        _reply_text(event, client, f"Error: {exc}")


def _is_direct_message(event: dict, client) -> bool:
    """True for 1:1 or multi-party DM with the app (not ordinary channels)."""
    if event.get("channel_type") in ("im", "mpim"):
        return True
    ch = event.get("channel")
    if not ch:
        return False
    # 1:1 DM channels start with D (avoids API for most DMs).
    if ch.startswith("D"):
        return True
    # G can be private channel or multi-party DM — disambiguate only when needed.
    try:
        info = client.conversations_info(channel=ch)
        c = info.get("channel") or {}
        return bool(c.get("is_im") or c.get("is_mpim"))
    except Exception:
        return False


def create_app() -> App:
    signing = (os.environ.get("SLACK_SIGNING_SECRET") or "").strip() or None
    bot_token = (os.environ.get("SLACK_BOT_TOKEN") or "").strip() or None
    app = App(
        token=bot_token,
        signing_secret=signing,
    )

    @app.event("app_mention")
    def on_mention(event, client, logger):
        _process_user_text(event, client, event.get("text") or "", logger)

    @app.event("message")
    def on_dm_message(event, client, logger):
        # app_mention is often NOT sent for 1:1 DMs with the bot — those are `message` events.
        if event.get("bot_id") or event.get("subtype"):
            return
        if not _is_direct_message(event, client):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        _process_user_text(event, client, text, logger)

    return app


def main() -> None:
    if not (os.environ.get("SLACK_BOT_TOKEN") or "").strip():
        print("SLACK_BOT_TOKEN is required", file=sys.stderr)
        sys.exit(1)

    app = create_app()
    app_token = (os.environ.get("SLACK_APP_TOKEN") or "").strip() or None

    if app_token:
        logger.info("Starting Socket Mode handler")
        SocketModeHandler(app, app_token).start()
        return

    signing = (os.environ.get("SLACK_SIGNING_SECRET") or "").strip() or None
    if not signing:
        print(
            "Either SLACK_APP_TOKEN (Socket Mode) or SLACK_SIGNING_SECRET (HTTP) is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    port = int(os.environ.get("PORT", "3000"))
    logger.info("Starting HTTP receiver on port %s", port)
    app.start(port=port)


if __name__ == "__main__":
    main()
