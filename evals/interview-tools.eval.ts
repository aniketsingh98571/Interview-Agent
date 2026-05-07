import { evaluate } from "@lmnr-ai/lmnr";
import dataset from "./data/interview-tools.json" with { type: "json" };
import type { EvalData, EvalTarget, SingleTurnDatasetEntry, SingleTurnResult } from "./types.ts";
import { executeSingleTurnEvalWithMockTools } from "./executors.ts";
import { toolSelectionScore } from "./evaluators.ts";

const executor = async (data: EvalData, _target: EvalTarget) => {
  return executeSingleTurnEvalWithMockTools(data);
};

// TS infers widened types from JSON; assert intended dataset shape.
const typedDataset = dataset as unknown as SingleTurnDatasetEntry[];

evaluate({
  data: typedDataset,
  executor,
  evaluators: {
    selectionScore: (output: SingleTurnResult, target) => {
      if (!target) return 0;
      if (target.category === "secondary") return 1;
      return toolSelectionScore(output, target);
    },
  },
  groupName: "interview-tools",
  config: { projectApiKey: process.env.LMNR_PROJECT_API_KEY ?? process.env.LMNR_API_KEY },
});

