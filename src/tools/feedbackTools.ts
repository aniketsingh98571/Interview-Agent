import { z } from "zod";
import { getFeedback, listRounds, saveFeedback } from "../storage/feedbackStore.js";
import { GetFeedbackArgsSchema, ListRoundsArgsSchema, SaveFeedbackArgsSchema } from "./toolSpecs.js";

export type SaveFeedbackArgs = z.infer<typeof SaveFeedbackArgsSchema>;
export type GetFeedbackArgs = z.infer<typeof GetFeedbackArgsSchema>;
export type ListRoundsArgs = z.infer<typeof ListRoundsArgsSchema>;

export async function tool_save_feedback(args: SaveFeedbackArgs) {
  const parsed = SaveFeedbackArgsSchema.parse(args);
  const res = await saveFeedback(parsed);
  return res;
}

export async function tool_list_rounds(args: ListRoundsArgs) {
  const parsed = ListRoundsArgsSchema.parse(args);
  if (!parsed.candidateName) return { ok: false as const, error: "Candidate name is required.", rounds: [] };
  return await listRounds(parsed.candidateName);
}

export async function tool_get_feedback(args: GetFeedbackArgs) {
  const parsed = GetFeedbackArgsSchema.parse(args);
  if (!parsed.candidateName) {
    return { ok: false as const, error: "Candidate name is required.", availableRounds: [] };
  }
  // Critical behavior: If round missing → DO NOT fetch; return available rounds.
  if (!parsed.round) {
    const rounds = await listRounds(parsed.candidateName);
    if (!rounds.ok) {
      return { ok: false as const, error: rounds.error, availableRounds: rounds.rounds ?? [] };
    }
    return { ok: false as const, error: "Round is required.", availableRounds: rounds.rounds };
  }
  return await getFeedback(parsed.candidateName, parsed.round);
}

