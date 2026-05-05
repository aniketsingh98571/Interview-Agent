import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Repo-local data directory (relative to this file: src/storage -> ../../data)
export const DATA_DIR = path.resolve(__dirname, "../../data");
export const FEEDBACK_DIR = path.resolve(DATA_DIR, "feedback");
export const THREAD_STATE_DIR = path.resolve(DATA_DIR, "thread_state");

