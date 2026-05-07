import { App, ExpressReceiver } from "@slack/bolt";
import type { Env } from "../config/env.js";
import { runAgent } from "../agent/agent.js";

function formatSlackMrkdwn(markdown: string): string {
  let t = String(markdown ?? "");

  // Remove fenced code blocks (Slack would render them as a big grey code box).
  // Keep the content; most of our outputs are regular markdown, not actual code.
  t = t.replace(/```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)\n?```\s*/g, (_m, inner) => String(inner ?? ""));

  // Convert Markdown bold (**text**) to Slack bold (*text*).
  // Avoid touching already-Slack bold markers.
  t = t.replace(/\*\*([^*\n]+)\*\*/g, (_m, inner) => `*${inner}*`);

  // Slack mrkdwn does not support Markdown headings; turn them into bold lines.
  // e.g. "## Title" -> "*Title*"
  t = t.replace(/^\s{0,3}#{1,6}\s+(.+?)\s*$/gm, (_m, title) => `*${title}*`);

  // Slack list rendering is sensitive to indentation; normalize common cases.
  t = t.replace(/^\s{2,}(-\s+)/gm, "$1");

  return t;
}

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

function slackBlocksFromMarkdown(markdown: string): any[] {
  // Section blocks are limited to 3000 characters.
  const maxChunkLen = 2900;
  const text = formatSlackMrkdwn(slackSafeText(markdown));

  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxChunkLen) {
    chunks.push(text.slice(i, i + maxChunkLen));
  }

  return chunks.map((chunk) => ({
    type: "section",
    text: { type: "mrkdwn", text: chunk || " " },
  }));
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
          blocks: slackBlocksFromMarkdown("Say something after the mention. Try `help`."),
        });
        return;
      }

      if (isHelpMessage(cleaned)) {
        await client.chat.postMessage({
          channel: (event as any).channel,
          thread_ts: (event as any).thread_ts ?? (event as any).ts,
          text: helpText(),
          blocks: slackBlocksFromMarkdown(helpText()),
        });
        return;
      }

      const threadContext = await buildThreadContext(client, event);

      const placeholderText = "_Thinking…_";
      const placeholder = await client.chat.postMessage({
        channel: (event as any).channel,
        thread_ts: (event as any).thread_ts ?? (event as any).ts,
        text: placeholderText,
        blocks: slackBlocksFromMarkdown(placeholderText),
      });

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

      await client.chat.update({
        channel: (event as any).channel,
        ts: String((placeholder as any)?.ts ?? ""),
        text: slackSafeText(out),
        blocks: slackBlocksFromMarkdown(out),
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
          blocks: slackBlocksFromMarkdown(helpText()),
        });
        return;
      }

      const threadContext = await buildThreadContext(client, ev);

      const placeholderText = "_Thinking…_";
      const placeholder = await client.chat.postMessage({
        channel: ev.channel,
        thread_ts: ev.thread_ts ?? ev.ts,
        text: placeholderText,
        blocks: slackBlocksFromMarkdown(placeholderText),
      });

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

      await client.chat.update({
        channel: ev.channel,
        ts: String((placeholder as any)?.ts ?? ""),
        text: slackSafeText(out),
        blocks: slackBlocksFromMarkdown(out),
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
        await respond({
          text: "Usage: `/feedback <your message>` (e.g. `/feedback get feedback John`)",
          blocks: slackBlocksFromMarkdown(
            "Usage: `/feedback <your message>` (e.g. `/feedback get feedback John`)",
          ),
        });
        return;
      }

      if (isHelpMessage(text)) {
        await respond({ text: helpText(), blocks: slackBlocksFromMarkdown(helpText()) });
        return;
      }

      const placeholderText = "_Thinking…_";
      await respond({
        text: placeholderText,
        blocks: slackBlocksFromMarkdown(placeholderText),
      });

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

      await respond({
        replace_original: true,
        text: slackSafeText(out),
        blocks: slackBlocksFromMarkdown(out),
      });
    } catch (e: any) {
      logger?.error(e);
      const msg = `Error: ${String(e?.message ?? e)}`;
      await respond({ replace_original: true, text: msg, blocks: slackBlocksFromMarkdown(msg) });
    }
  });
}

