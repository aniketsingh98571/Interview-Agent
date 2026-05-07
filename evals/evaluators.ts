import { generateObject, generateText } from "ai";
import { z } from "zod";
import type { EvalTarget, MultiTurnResult, MultiTurnTarget, SingleTurnResult } from "./types.ts";
import { getOllamaModel } from "./llm/ollama.ts";

export function toolSelectionScore(output: SingleTurnResult, target: EvalTarget): number {
  if (!target.expectedTools?.length) return output.selectedAny ? 0.5 : 1;

  const expected = new Set(target.expectedTools);
  const selected = new Set(output.toolNames);

  const hits = output.toolNames.filter((t) => expected.has(t)).length;
  const precision = selected.size > 0 ? hits / selected.size : 0;
  const recall = expected.size > 0 ? hits / expected.size : 0;
  if (precision + recall === 0) return 0;
  return (2 * precision * recall) / (precision + recall);
}

const judgeSchema = z.object({
  score: z.number().min(0).max(10),
  reason: z.string(),
});

export const llmJudge = async (output: MultiTurnResult, target: MultiTurnTarget) => {
  const model = getOllamaModel();
  const messages = [
    {
      role: "system" as const,
      content: `You are an evaluation judge. Score the agent's response on a scale of 0-10.

Scoring criteria:
- 10: Fully addresses the task and uses tool results correctly
- 7-9: Mostly correct with minor issues
- 4-6: Partially addresses the task
- 0-3: Incorrect or irrelevant

Return ONLY a JSON object with keys "score" (0-10 number) and "reason" (string).`,
    },
    {
      role: "user" as const,
      content: `Task: ${target.originalTask}
Tools Called: ${JSON.stringify(output.toolCallOrder)}
Mock Tool Results: ${JSON.stringify(target.mockToolResults)}
Agent Final Response: ${output.text}`,
    },
  ];

  try {
    const result = await generateObject({
      model,
      schema: judgeSchema,
      schemaName: "evaluation",
      schemaDescription: "Evaluation of agent output quality",
      messages,
      temperature: 0,
    });
    return result.object.score / 10;
  } catch {
    const { text } = await generateText({ model, messages, temperature: 0 });
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) return 0;
    try {
      const parsed = JSON.parse(jsonMatch[0]);
      const validated = judgeSchema.safeParse(parsed);
      if (!validated.success) return 0;
      return validated.data.score / 10;
    } catch {
      return 0;
    }
  }
};

