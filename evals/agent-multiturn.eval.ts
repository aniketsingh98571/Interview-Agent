import { evaluate } from "@lmnr-ai/lmnr";
import dataset from "./data/agent-multiturn.json" with { type: "json" };
import type { MultiTurnDatasetEntry, MultiTurnEvalData } from "./types.ts";
import { multiTurnWithMocks } from "./executors.ts";
import { llmJudge } from "./evaluators.ts";

const executor = async (data: MultiTurnEvalData) => {
  return await multiTurnWithMocks(data);
};

const typedDataset = dataset as unknown as MultiTurnDatasetEntry[];

evaluate({
  data: typedDataset,
  executor,
  evaluators: {
    outputQuality: async (output, target) => {
      if (!target) return 1;
      return llmJudge(output, target);
    },
  },
  groupName: "agent-multiturn",
  config: { projectApiKey: process.env.LMNR_PROJECT_API_KEY ?? process.env.LMNR_API_KEY },
});

