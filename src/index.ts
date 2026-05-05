import dotenv from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { getEnv } from "./config/env.js";
import { createSlackApp } from "./slack/slackApp.js";

// Load ONLY interview_feedback_agent/.env (regardless of cwd)
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../.env") });

async function main() {
  const env = getEnv(process.env);
  const { start } = createSlackApp(env);
  await start();
  // eslint-disable-next-line no-console
  console.log(`Slack agent running on port ${env.PORT}`);
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});

