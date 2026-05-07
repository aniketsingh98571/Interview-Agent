import { z } from "zod";

export const SaveFeedbackArgsSchema = z.object({
  candidateName: z.string().min(1),
  round: z.string().min(1),
  interviewer: z.string().min(1),
  feedback: z.string().min(1),
});

export const ListRoundsArgsSchema = z.object({
  candidateName: z.string().min(1).optional(),
});

export const GetFeedbackArgsSchema = z.object({
  candidateName: z.string().min(1).optional(),
  round: z.string().min(1).optional(),
});

export const WebSearchArgsSchema = z.object({
  query: z.string().min(1),
});

export const ClearThreadStateArgsSchema = z.object({
  confirm: z.boolean().optional(),
});

export const TOOL_SPECS = {
  save_feedback: {
    description: "Save interviewer feedback for a candidate and round as a structured markdown file.",
    inputSchema: SaveFeedbackArgsSchema,
  },
  list_rounds: {
    description: "List all available feedback rounds for a candidate (without fetching any feedback).",
    inputSchema: ListRoundsArgsSchema,
  },
  get_feedback: {
    description:
      "Get feedback markdown for a candidate for a specific round. If round is missing, do NOT fetch; return available rounds instead.",
    inputSchema: GetFeedbackArgsSchema,
  },
  web_search: {
    description: "Search the web for up-to-date info to help generate follow-up interview questions.",
    inputSchema: WebSearchArgsSchema,
  },
  clear_thread_state: {
    description: "Clear the stored conversation state for this Slack thread.",
    inputSchema: ClearThreadStateArgsSchema,
  },
} as const;

