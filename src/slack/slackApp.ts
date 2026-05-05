import { App, ExpressReceiver } from "@slack/bolt";
import type { Env } from "../config/env.js";
import { runAgent } from "../agent/agent.js";

function helpText(): string {
  return [
    "*Interview Feedback Agent*",
    "",
    "*Save feedback*",
    "- `@Interview Agent save feedback John Doe round 1 dsa interviewer Alice: <notes>`",
    "- In a thread: reply under the write-up with `@Interview Agent record this for John Doe, interviewer Alice, round 1`",
    "",
    "*Retrieve feedback*",
    "- `@Interview Agent get feedback John Doe` (will ask which round if needed)",
    "- `@Interview Agent get feedback John Doe round 1`",
    "",
    "*Rounds*",
    "- `@Interview Agent list rounds for John Doe`",
    "",
    "*Follow-up questions*",
    "- `@Interview Agent what questions should I ask this candidate?` (will retrieve feedback + do web search)",
    "",
    "_Files are stored under `data/feedback/` and state under `data/thread_state/`._",
  ].join("\n");
}

function isHelpMessage(text: string): boolean {
  const t = (text ?? "").trim().toLowerCase();
  return t === "help" || t === "?" || t === "commands" || t === "usage";
}

function stripBotMention(text: string): string {
  // Remove "<@U123...>" at the start, if present.
  const t = (text ?? "").trim();
  return t.replace(/^<@[^>]+>\s*/, "").trim();
}

function threadKeyFromEvent(event: any): { channelId: string; threadTs: string } {
  const channelId = String(event.channel);
  const threadTs = String(event.thread_ts ?? event.ts);
  return { channelId, threadTs };
}

function slackSafeText(text: string): string {
  const maxLen = 39_000;
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen - 80) + "\n\n_(truncated for Slack length limits)_";
}

async function buildThreadContext(client: any, event: any): Promise<string> {
  const channel = String(event?.channel ?? "");
  const curTs = String(event?.ts ?? "");
  if (!channel || !curTs) return "";

  const lines: string[] = [];
  const threadTs = event?.thread_ts ? String(event.thread_ts) : "";

  try {
    if (threadTs) {
      const resp = await client.conversations.replies({ channel, ts: threadTs, limit: 50 });
      for (const m of resp?.messages ?? []) {
        if (!m) continue;
        if (String(m.ts ?? "") === curTs) continue;
        if (m.bot_id) continue;
        if (m.subtype && ["channel_join", "channel_leave", "channel_topic"].includes(String(m.subtype))) continue;
        const txt = String(m.text ?? "").trim();
        if (txt) lines.push(txt);
      }
    } else {
      const resp = await client.conversations.history({
        channel,
        latest: curTs,
        limit: 12,
        inclusive: false,
      });
      const msgs = Array.isArray(resp?.messages) ? resp.messages : [];
      for (const m of msgs.reverse()) {
        if (!m) continue;
        if (m.bot_id) continue;
        if (m.subtype) continue;
        const txt = String(m.text ?? "").trim();
        if (txt) lines.push(txt);
      }
    }
  } catch {
    return "";
  }

  const out = lines.join("\n\n");
  if (out.length > 14_000) return out.slice(0, 14_000) + "\n\n_(truncated)_";
  return out;
}

export function createSlackApp(env: Env): { app: App; start: () => Promise<void> } {
  const wantSocketMode = Boolean(env.SLACK_APP_TOKEN && env.SLACK_APP_TOKEN.trim());

  if (wantSocketMode) {
    const app = new App({
      token: env.SLACK_BOT_TOKEN,
      socketMode: true,
      appToken: env.SLACK_APP_TOKEN!,
    });

    registerHandlers(app, env);

    return {
      app,
      start: async () => {
        await app.start(env.PORT);
      },
    };
  }

  if (!env.SLACK_SIGNING_SECRET) {
    throw new Error("Either SLACK_APP_TOKEN (Socket Mode) or SLACK_SIGNING_SECRET (HTTP) must be set.");
  }

  const receiver = new ExpressReceiver({
    signingSecret: env.SLACK_SIGNING_SECRET,
    endpoints: "/slack/events",
  });

  const app = new App({
    token: env.SLACK_BOT_TOKEN,
    receiver,
  });

  registerHandlers(app, env);

  return {
    app,
    start: async () => {
      await app.start(env.PORT);
    },
  };
}

function registerHandlers(app: App, env: Env) {
  app.event("app_mention", async ({ event, client, logger }) => {
    try {
      const cleaned = stripBotMention(String((event as any).text ?? ""));
      if (!cleaned) {
        await client.chat.postMessage({
          channel: (event as any).channel,
          thread_ts: (event as any).thread_ts ?? (event as any).ts,
          text: "Say something after the mention. Try `help`.",
          mrkdwn: true,
        });
        return;
      }

      if (isHelpMessage(cleaned)) {
        await client.chat.postMessage({
          channel: (event as any).channel,
          thread_ts: (event as any).thread_ts ?? (event as any).ts,
          text: helpText(),
          mrkdwn: true,
        });
        return;
      }

      const threadContext = await buildThreadContext(client, event);
      const out = await runAgent(cleaned, {
        env: {
          OLLAMA_BASE_URL: env.OLLAMA_BASE_URL,
          OLLAMA_API_KEY: env.OLLAMA_API_KEY,
          OLLAMA_MODEL: env.OLLAMA_MODEL,
          TAVILY_API_KEY: env.TAVILY_API_KEY,
        },
        thread: threadKeyFromEvent(event),
        threadContext,
      });

      await client.chat.postMessage({
        channel: (event as any).channel,
        thread_ts: (event as any).thread_ts ?? (event as any).ts,
        text: slackSafeText(out),
        mrkdwn: true,
      });
    } catch (e: any) {
      logger?.error(e);
    }
  });

  // DMs (message.im) typically arrive as "message" events; filter for DMs only.
  app.event("message", async ({ event, client, logger }) => {
    try {
      const ev: any = event;
      if (ev.bot_id || ev.subtype) return;
      if (ev.channel_type !== "im" && !String(ev.channel ?? "").startsWith("D")) return;
      const text = String(ev.text ?? "").trim();
      if (!text) return;

      if (isHelpMessage(text)) {
        await client.chat.postMessage({
          channel: ev.channel,
          thread_ts: ev.thread_ts ?? ev.ts,
          text: helpText(),
          mrkdwn: true,
        });
        return;
      }

      const threadContext = await buildThreadContext(client, ev);
      const out = await runAgent(text, {
        env: {
          OLLAMA_BASE_URL: env.OLLAMA_BASE_URL,
          OLLAMA_API_KEY: env.OLLAMA_API_KEY,
          OLLAMA_MODEL: env.OLLAMA_MODEL,
          TAVILY_API_KEY: env.TAVILY_API_KEY,
        },
        thread: threadKeyFromEvent(ev),
        threadContext,
      });

      await client.chat.postMessage({
        channel: ev.channel,
        thread_ts: ev.thread_ts ?? ev.ts,
        text: slackSafeText(out),
        mrkdwn: true,
      });
    } catch (e: any) {
      logger?.error(e);
    }
  });

  // Slash command is a clean “API surface” for production use.
  app.command("/feedback", async ({ command, ack, respond, logger }) => {
    await ack();
    try {
      const text = String(command.text ?? "").trim();
      if (!text) {
        await respond("Usage: `/feedback <your message>` (e.g. `/feedback get feedback John`)");
        return;
      }

      if (isHelpMessage(text)) {
        await respond(helpText());
        return;
      }

      const out = await runAgent(text, {
        env: {
          OLLAMA_BASE_URL: env.OLLAMA_BASE_URL,
          OLLAMA_API_KEY: env.OLLAMA_API_KEY,
          OLLAMA_MODEL: env.OLLAMA_MODEL,
          TAVILY_API_KEY: env.TAVILY_API_KEY,
        },
        thread: { channelId: command.channel_id, threadTs: command.trigger_id },
        threadContext: "",
      });

      await respond(slackSafeText(out));
    } catch (e: any) {
      logger?.error(e);
      await respond(`Error: ${String(e?.message ?? e)}`);
    }
  });
}

