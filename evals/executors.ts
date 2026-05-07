import { generateText, stepCountIs, tool, type ToolSet } from "ai";
import type { EvalData, MultiTurnEvalData, MultiTurnResult, SingleTurnResult } from "./types.ts";
import { getOllamaModel } from "./llm/ollama.ts";
import { buildMessages, buildMockedTools } from "./utils.ts";
import { TOOL_SPECS } from "../src/tools/toolSpecs.ts";

const TOOL_DEFINITIONS = {
  save_feedback: tool({
    description: TOOL_SPECS.save_feedback.description,
    inputSchema: TOOL_SPECS.save_feedback.inputSchema,
  }),
  list_rounds: tool({
    description: TOOL_SPECS.list_rounds.description,
    inputSchema: TOOL_SPECS.list_rounds.inputSchema,
  }),
  get_feedback: tool({
    description: TOOL_SPECS.get_feedback.description,
    inputSchema: TOOL_SPECS.get_feedback.inputSchema,
  }),
  web_search: tool({
    description: TOOL_SPECS.web_search.description,
    inputSchema: TOOL_SPECS.web_search.inputSchema,
  }),
  clear_thread_state: tool({
    description: TOOL_SPECS.clear_thread_state.description,
    inputSchema: TOOL_SPECS.clear_thread_state.inputSchema,
  }),
} as const;

export const executeSingleTurnEvalWithMockTools = async (data: EvalData): Promise<SingleTurnResult> => {
  const messages = buildMessages(data);

  // Select tool schemas dynamically; loosen typing for the harness.
  const tools = {} as ToolSet;
  for (const toolName of data.tools) {
    const def = TOOL_DEFINITIONS[toolName as keyof typeof TOOL_DEFINITIONS];
    if (def) {
      (tools as any)[toolName] = tool({
        description: def.description,
        inputSchema: def.inputSchema as any,
      });
    }
  }

  const { toolCalls } = await generateText({
    model: getOllamaModel(data.config?.model),
    messages,
    tools,
    temperature: data.config?.temperature,
    stopWhen: stepCountIs(1),
  });

  const calls = toolCalls.map((call) => ({
    toolName: call.toolName,
    args: "args" in call ? call.args : {},
  }));
  const names = calls.map((c) => c.toolName);
  return { toolCalls: calls, toolNames: names, selectedAny: names.length > 0 };
};

export const multiTurnWithMocks = async (data: MultiTurnEvalData): Promise<MultiTurnResult> => {
  const tools = buildMockedTools(data.mockTools);

  const messages = data.messages ?? buildMessages({ prompt: data.prompt ?? "" });

  const result = await generateText({
    model: getOllamaModel(data.config?.model),
    messages,
    tools,
    stopWhen: stepCountIs(data.config?.maxSteps ?? 20),
  });

  const steps = result.steps.map((step) => ({
    text: step.text,
    toolCalls: step.toolCalls?.map((call) => ({
      toolName: call.toolName,
      args: "args" in call ? call.args : {},
    })),
    toolResults: step.toolResults?.map((tr) => ({
      toolName: tr.toolName,
      result: (tr as any).result ?? (tr as any).output ?? tr,
    })),
  }));

  const toolCallOrder = steps.flatMap((s) => s.toolCalls?.map((c) => c.toolName) ?? []);
  const toolsUsed = Array.from(new Set(toolCallOrder));

  return { text: result.text, steps, toolsUsed, toolCallOrder };
};

