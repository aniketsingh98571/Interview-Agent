import fs from "node:fs/promises";
import path from "node:path";
import { THREAD_STATE_DIR } from "../storage/paths.js";
import { normalizeCandidateName } from "../utils/slug.js";

export type ThreadKey = {
  channelId: string;
  threadTs: string; // thread timestamp (or event ts for non-threaded)
};

export type ConversationState = {
  candidateName?: string;
  selectedRound?: string;
  updatedAtISO: string;
};

function fileNameForThread({ channelId, threadTs }: ThreadKey): string {
  const safe = `${channelId}__${threadTs}`.replace(/[^a-zA-Z0-9_.-]+/g, "_");
  return path.join(THREAD_STATE_DIR, `${safe}.json`);
}

async function ensureDir(p: string) {
  await fs.mkdir(p, { recursive: true });
}

export async function getThreadState(key: ThreadKey): Promise<ConversationState> {
  await ensureDir(THREAD_STATE_DIR);
  const fp = fileNameForThread(key);
  try {
    const raw = await fs.readFile(fp, "utf8");
    const parsed = JSON.parse(raw) as ConversationState;
    if (!parsed || typeof parsed !== "object") throw new Error("bad state");
    return parsed;
  } catch (e: any) {
    if (e?.code === "ENOENT") return { updatedAtISO: new Date().toISOString() };
    return { updatedAtISO: new Date().toISOString() };
  }
}

export async function setThreadState(key: ThreadKey, patch: Partial<Omit<ConversationState, "updatedAtISO">>) {
  await ensureDir(THREAD_STATE_DIR);
  const current = await getThreadState(key);
  const next: ConversationState = {
    ...current,
    ...patch,
    candidateName: patch.candidateName ? normalizeCandidateName(patch.candidateName) : current.candidateName,
    updatedAtISO: new Date().toISOString(),
  };
  await fs.writeFile(fileNameForThread(key), JSON.stringify(next, null, 2), "utf8");
  return next;
}

export async function clearThreadState(key: ThreadKey) {
  const fp = fileNameForThread(key);
  try {
    await fs.unlink(fp);
  } catch (e: any) {
    if (e?.code === "ENOENT") return;
    throw e;
  }
}

