import type { ModelMessage } from "ai";

export interface EvalData {
  prompt: string;
  systemPrompt?: string;
  tools: string[];
  config?: {
    model?: string;
    temperature?: number;
  };
}

export interface EvalTarget {
  expectedTools?: string[];
  forbiddenTools?: string[];
  category: "golden" | "secondary" | "negative";
}

export interface SingleTurnResult {
  toolCalls: Array<{ toolName: string; args: unknown }>;
  toolNames: string[];
  selectedAny: boolean;
}

export interface MockToolConfig {
  description: string;
  parameters: Record<string, string>;
  mockReturn: string;
}

export interface MultiTurnEvalData {
  prompt?: string;
  messages?: ModelMessage[];
  mockTools: Record<string, MockToolConfig>;
  config?: {
    model?: string;
    maxSteps?: number;
  };
}

export interface MultiTurnTarget {
  originalTask: string;
  expectedToolOrder?: string[];
  forbiddenTools?: string[];
  mockToolResults: Record<string, string>;
  category: "task-completion" | "conversation-continuation" | "negative";
}

export interface MultiTurnResult {
  text: string;
  steps: Array<{
    toolCalls?: Array<{ toolName: string; args: unknown }>;
    toolResults?: Array<{ toolName: string; result: unknown }>;
    text?: string;
  }>;
  toolsUsed: string[];
  toolCallOrder: string[];
}

export interface SingleTurnDatasetEntry {
  data: EvalData;
  target: EvalTarget;
  metadata?: { description?: string };
}

export interface MultiTurnDatasetEntry {
  data: MultiTurnEvalData;
  target: MultiTurnTarget;
  metadata?: { description?: string };
}

