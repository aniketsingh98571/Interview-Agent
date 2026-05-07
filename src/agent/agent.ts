import { extractJsonMiddleware, generateText, Output, stepCountIs, tool, wrapLanguageModel } from "ai";
import { createOllama } from "ollama-ai-provider-v2";
import { z } from "zod";
import type { ConversationState, ThreadKey } from "../state/threadState.js";
import { clearThreadState, getThreadState, setThreadState } from "../state/threadState.js";
import { SYSTEM_PROMPT } from "./prompt.js";
import { tool_get_feedback, tool_list_rounds } from "../tools/feedbackTools.js";
import {
  ClearThreadStateArgsSchema,
  GetFeedbackArgsSchema,
  ListRoundsArgsSchema,
  SaveFeedbackArgsSchema,
  TOOL_SPECS,
  WebSearchArgsSchema,
} from "../tools/toolSpecs.js";
import { webSearch } from "../tools/webSearch.js";
import { saveFeedback } from "../storage/feedbackStore.js";

export type AgentContext = {
  env: {
    OLLAMA_BASE_URL: string;
    OLLAMA_API_KEY?: string;
    OLLAMA_MODEL: string;
    TAVILY_API_KEY?: string;
  };
  thread: ThreadKey;
};

const AgentInputSchema = z.object({
  text: z.string().min(1),
  threadContext: z.string().optional(),
});

function stateToText(state: ConversationState): string {
  const cand = state.candidateName ? `candidateName=${JSON.stringify(state.candidateName)}` : "candidateName=(unset)";
  const rnd = state.selectedRound ? `selectedRound=${JSON.stringify(state.selectedRound)}` : "selectedRound=(unset)";
  return `${cand}, ${rnd}`;
}

export async function runAgent(rawText: string, ctx: AgentContext & { threadContext?: string }): Promise<string> {
  const { text, threadContext } = AgentInputSchema.parse({ text: rawText, threadContext: ctx.threadContext });
  const state = await getThreadState(ctx.thread);

  const baseURL = ctx.env.OLLAMA_BASE_URL.replace(/\/+$/g, "");
  const providerBaseURL = baseURL.endsWith("/api") ? baseURL : `${baseURL}/api`;
  const headers =
    ctx.env.OLLAMA_API_KEY && ctx.env.OLLAMA_API_KEY.trim()
      ? { Authorization: `Bearer ${ctx.env.OLLAMA_API_KEY.trim()}` }
      : undefined;
  const ollama = createOllama({ baseURL: providerBaseURL, headers });
  const model = ollama(ctx.env.OLLAMA_MODEL);

  async function doSaveFeedback(args: z.infer<typeof SaveFeedbackArgsSchema>) {
    const ExtractSchema = z.object({
      strengths: z.string(),
      weaknesses: z.string(),
      verdict: z.string(),
    });

    let extractedObj: z.infer<typeof ExtractSchema> = { strengths: "", weaknesses: "", verdict: "" };
    console.log(args.feedback,"args.feedback")
    try {
      const extractionModel = wrapLanguageModel({
        model,
        middleware: extractJsonMiddleware(),
      });

      const extracted = await generateText({
        model: extractionModel,
        output: Output.object({ schema: ExtractSchema }),
        prompt: `You are given a candidate's interviewer feedback (free-form text).

Extract evidence-based bullet-style summaries into these fields:
- strengths: only what the feedback explicitly supports as positives
- weaknesses: only what the feedback explicitly supports as concerns/negatives
- verdict: if the feedback implies a select/hold/no-select opinion, capture it; otherwise return empty.

Rules:
- Do NOT add or infer facts that are not present.
- Rephrase using wording from the feedback as much as possible.
- If a field is not supported by the feedback, return "" for that field.
- Return ONLY a valid JSON object with exactly these keys: strengths, weaknesses, verdict (string values).

Feedback:
${args.feedback}
`,
      });
      extractedObj = extracted.output ?? { strengths: "", weaknesses: "", verdict: "" };
    } catch (err) {
      console.error("Feedback extraction failed:", err);
      // If extraction fails, still save the feedback; leave structured fields empty.
      extractedObj = { strengths: "", weaknesses: "", verdict: "" };
    }
console.log(extractedObj,"extractedObj")
    const res = await saveFeedback({
      candidateName: args.candidateName,
      round: args.round,
      interviewer: args.interviewer,
      feedback: args.feedback,
      strengths: extractedObj.strengths ?? "",
      weaknesses: extractedObj.weaknesses ?? "",
      verdict: extractedObj.verdict ?? "",
    });

    await setThreadState(ctx.thread, { candidateName: res.candidateName, selectedRound: args.round });
    return res;
  }

  async function doListRounds(args: z.infer<typeof ListRoundsArgsSchema>) {
    const candidateName = args.candidateName ?? state.candidateName;
    if (!candidateName) return { ok: false as const, error: "Which candidate?" };
    const res = await tool_list_rounds({ candidateName });
    if (res.ok) await setThreadState(ctx.thread, { candidateName: res.candidateName });
    return res;
  }

  async function doGetFeedback(args: z.infer<typeof GetFeedbackArgsSchema>) {
    const candidateName = args.candidateName ?? state.candidateName;
    if (!candidateName) return { ok: false as const, error: "Which candidate?", availableRounds: [] };
    const res = await tool_get_feedback({ candidateName, round: args.round });
    if ((res as any).ok === true) {
      await setThreadState(ctx.thread, { candidateName, selectedRound: args.round });
    }
    return res;
  }

  async function doWebSearch(args: z.infer<typeof WebSearchArgsSchema>) {
    return webSearch(args, { TAVILY_API_KEY: ctx.env.TAVILY_API_KEY });
  }

  async function doClearThreadState() {
    await clearThreadState(ctx.thread);
    return { ok: true as const };
  }

  function tryParseThreadSaveCommand(t: string): { candidateName: string; interviewer: string; round: string } | null {
    const text = (t ?? "").toLowerCase();
    if (!(text.includes("record this") || text.includes("save this") || text.includes("save") || text.includes("record")))
      return null;

    const cand =
      t.match(/candidate[-:\s]*([a-zA-Z0-9 _-]+)/i)?.[1]?.trim() ??
      t.match(/for\s+([a-zA-Z][a-zA-Z0-9 _-]+)/i)?.[1]?.trim();
    const interviewer = t.match(/interviewer[-:\s]*([a-zA-Z0-9 _-]+)/i)?.[1]?.trim();
    const round = t.match(/round[-:\s]*([a-zA-Z0-9 _-]+)/i)?.[1]?.trim();
    if (!cand || !interviewer || !round) return null;
    return { candidateName: cand, interviewer, round };
  }

  const tools = {
    save_feedback: tool({
      description: TOOL_SPECS.save_feedback.description,
      inputSchema: SaveFeedbackArgsSchema,
      execute: async (args) => doSaveFeedback(args),
    }),

    list_rounds: tool({
      description: TOOL_SPECS.list_rounds.description,
      inputSchema: ListRoundsArgsSchema,
      execute: async (args) => doListRounds(args),
    }),

    get_feedback: tool({
      description: TOOL_SPECS.get_feedback.description,
      inputSchema: GetFeedbackArgsSchema,
      execute: async (args) => doGetFeedback(args),
    }),

    web_search: tool({
      description: TOOL_SPECS.web_search.description,
      inputSchema: WebSearchArgsSchema,
      execute: async (args) => doWebSearch(args),
    }),

    clear_thread_state: tool({
      description: TOOL_SPECS.clear_thread_state.description,
      inputSchema: ClearThreadStateArgsSchema,
      execute: async () => doClearThreadState(),
    }),
  };

  // Fallback execution path:
  // Some models (or configurations) may output a JSON tool call instead of using native tool calling.
  // If that happens, we detect it, execute the tool server-side, and return a human-friendly message.
  const ToolCallSchema = z.object({
    name: z.enum(["save_feedback", "get_feedback", "list_rounds", "web_search", "clear_thread_state"]),
    arguments: z.record(z.string(), z.any()).optional(),
  });

  async function executeToolCall(toolCall: z.infer<typeof ToolCallSchema>): Promise<string> {
    const name = toolCall.name;
    const args = toolCall.arguments ?? {};

    if (name === "save_feedback") {
      const parsedArgs = SaveFeedbackArgsSchema.parse(args);
      const res = await doSaveFeedback(parsedArgs);
      return `Saved feedback for *${res.candidateName}* (round *${parsedArgs.round}*, interviewer *${parsedArgs.interviewer}*).\nFile: \`${res.filePath}\``;
    }

    if (name === "list_rounds") {
      const parsedArgs = ListRoundsArgsSchema.parse(args);
      const res = await doListRounds(parsedArgs);
      if (!res.ok) return res.error;
      return `Available rounds for *${res.candidateName}*:\n- ${res.rounds.join("\n- ") || "(none)"}`;
    }

    if (name === "get_feedback") {
      const parsedArgs = GetFeedbackArgsSchema.parse(args);
      const res = await doGetFeedback(parsedArgs);
      if ((res as any).ok !== true) {
        const ar = (res as any).availableRounds as string[] | undefined;
        if (ar?.length) return `Which round?\n- ${ar.join("\n- ")}`;
        return (res as any).error ?? "Could not fetch feedback.";
      }
      const ok = res as any;
      const parts = ok.files
        .map((f: any) => `*${f.filePath}*\n${String(f.markdown).slice(0, 3500)}`)
        .join("\n\n---\n\n");
      return parts || "_No content._";
    }

    if (name === "web_search") {
      const parsedArgs = WebSearchArgsSchema.parse(args);
      const res = await doWebSearch(parsedArgs);
      return `Web search (${res.source}) for "${res.query}":\n${res.summary}`;
    }

    if (name === "clear_thread_state") {
      await doClearThreadState();
      return "Cleared thread state.";
    }

    return "Unsupported tool call.";
  }

  const userMessage = `
Slack thread state: ${stateToText(state)}

User message: ${text}

Thread context (previous messages in thread / recent history; may be empty):
${(threadContext ?? "").trim() || "(empty)"}
`;

  // Deterministic save-from-thread path:
  // If the user says "record this" and provides candidate/interviewer/round,
  // we use thread context as the feedback body and save immediately (no LLM/tool-calling needed).
  const direct = tryParseThreadSaveCommand(text);
  if (direct) {
    const ctxText = (threadContext ?? "").trim();
    if (!ctxText) {
      return "I couldn’t find any text in the thread to save. Please paste the feedback message in this thread, then say `record this ...` again.";
    }
    const res = await doSaveFeedback({
      candidateName: direct.candidateName,
      interviewer: direct.interviewer,
      round: direct.round,
      feedback: ctxText,
    });
    return `Saved feedback for *${res.candidateName}* (round *${direct.round}*, interviewer *${direct.interviewer}*).\nFile: \`${res.filePath}\``;
  }

  const result = await generateText({
    model,
    system: SYSTEM_PROMPT,
    prompt: userMessage,
    tools,
    stopWhen: stepCountIs(8),
  });
  // Tool calls are executed by the AI SDK when `tools` are provided.
  // If you need to detect whether tool calls happened, inspect `result.steps[*].toolCalls`.
  const stepsToolCalls = ((result as any)?.steps ?? [])
    .flatMap((s: any) => (Array.isArray(s?.toolCalls) ? s.toolCalls : []))
    .filter(Boolean);

  const out = result.text.trim();
  if (out) {
    // Fallback execution path:
    // Some models (or configurations) may output a JSON tool call as plain text instead of using native tool calling.
    // Only run this fallback if we did NOT already observe native tool calls in steps.
    try {
      if (stepsToolCalls.length === 0) {
        const maybeJson = out.startsWith("{") ? out : out.match(/\{[\s\S]*\}/)?.[0];
        if (maybeJson) {
          const parsed = ToolCallSchema.safeParse(JSON.parse(maybeJson));
          if (parsed.success) {
            return await executeToolCall(parsed.data);
          }
        }
      }
    } catch {
      // ignore JSON parsing errors; fall back to raw model text
    }
    return out;
  }
  return [
    "I didn’t generate a response (model returned empty).",
    "Try:",
    "- `help`",
    "- `save feedback John Doe round 1 dsa interviewer Alice: <notes>`",
    "- `get feedback John Doe`",
  ].join("\n");
}

