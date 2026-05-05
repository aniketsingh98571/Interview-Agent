import { z } from "zod";
import { getFeedback, listRounds, saveFeedback } from "../storage/feedbackStore.js";

export const SaveFeedbackSchema = z.object({
  candidateName: z.string().min(1),
  round: z.string().min(1),
  interviewer: z.string().min(1),
  feedback: z.string().min(1),
});

export const GetFeedbackSchema = z.object({
  candidateName: z.string().min(1),
  round: z.string().min(1).optional(),
});

export const ListRoundsSchema = z.object({
  candidateName: z.string().min(1),
});

export type SaveFeedbackArgs = z.infer<typeof SaveFeedbackSchema>;
export type GetFeedbackArgs = z.infer<typeof GetFeedbackSchema>;
export type ListRoundsArgs = z.infer<typeof ListRoundsSchema>;

export async function tool_save_feedback(args: SaveFeedbackArgs) {
  const parsed = SaveFeedbackSchema.parse(args);
  const res = await saveFeedback(parsed);
  return res;
}

export async function tool_list_rounds(args: ListRoundsArgs) {
  const parsed = ListRoundsSchema.parse(args);
  return await listRounds(parsed.candidateName);
}

export async function tool_get_feedback(args: GetFeedbackArgs) {
  const parsed = GetFeedbackSchema.parse(args);
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

