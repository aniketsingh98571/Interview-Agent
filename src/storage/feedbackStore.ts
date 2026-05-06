import fs from "node:fs/promises";
import path from "node:path";
import { FEEDBACK_DIR } from "./paths.js";
import { normalizeCandidateName, slugify } from "../utils/slug.js";

export type SaveFeedbackInput = {
  candidateName: string;
  round: string; // e.g. "1 dsa" or "round 1 hld"
  interviewer: string;
  feedback: string;
  strengths?: string;
  weaknesses?: string;
  verdict?: string;
};

export type SaveFeedbackResult = {
  ok: true;
  candidateName: string;
  candidateSlug: string;
  filePath: string;
  roundNumber: number | null;
  roundType: string;
  interviewerSlug: string;
  dateISO: string;
};

export type ListRoundsResult =
  | { ok: true; candidateName: string; candidateSlug: string; rounds: string[] }
  | { ok: false; error: string; rounds?: string[] };

export type GetFeedbackResult =
  | {
      ok: true;
      candidateName: string;
      candidateSlug: string;
      round: string;
      files: Array<{ filePath: string; markdown: string }>;
    }
  | { ok: false; error: string; availableRounds?: string[] };

function parseRound(roundRaw: string): { roundNumber: number | null; roundType: string; canonical: string } {
  const raw = (roundRaw ?? "").trim();
  const lowered = raw.toLowerCase();

  const ordinals: Record<string, number> = {
    first: 1,
    "1st": 1,
    second: 2,
    "2nd": 2,
    third: 3,
    "3rd": 3,
    fourth: 4,
    "4th": 4,
    fifth: 5,
    "5th": 5,
    sixth: 6,
    "6th": 6,
    seventh: 7,
    "7th": 7,
    eighth: 8,
    "8th": 8,
    ninth: 9,
    "9th": 9,
    tenth: 10,
    "10th": 10,
  };

  // Extract round number: supports "round 2", "2", "2nd", "first round", etc.
  const numMatch =
    lowered.match(/(?:^|\b)round[-\s]*([0-9]+)(?:st|nd|rd|th)?\b/i) ??
    lowered.match(/^([0-9]+)(?:st|nd|rd|th)?\b/i) ??
    lowered.match(/\b([0-9]+)(?:st|nd|rd|th)\b/i);

  let roundNumber: number | null = numMatch ? Number(numMatch[1]) : null;
  if (!roundNumber) {
    for (const [k, v] of Object.entries(ordinals)) {
      if (lowered.includes(k)) {
        roundNumber = v;
        break;
      }
    }
  }

  const knownTypes = [
    "dsa",
    "hld",
    "lld",
    "hm",
    "behavioral",
    "culture",
    "system",
    "sysdesign",
    "systemdesign",
    "coding",
    "frontend",
    "backend",
    "ml",
  ] as const;

  // Pick a round "type" token if present; fallback is derived from the full raw string.
  let roundTypeToken: string | undefined;
  for (const t of knownTypes) {
    if (new RegExp(`\\b${t}\\b`, "i").test(lowered)) {
      roundTypeToken = t;
      break;
    }
  }

  // If not a known type, attempt to use the first non-round token.
  if (!roundTypeToken) {
    const cleaned = lowered
      .replace(/\bround\b/g, " ")
      .replace(/\b([0-9]+)(?:st|nd|rd|th)?\b/g, " ")
      .replace(/\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b/g, " ")
      .trim();
    const token = cleaned.split(/[\s-]+/).find(Boolean);
    roundTypeToken = token || undefined;
  }

  const fallbackType = slugify(raw);
  const roundType = slugify(roundTypeToken ?? "").replace(/^unknown$/, "") || fallbackType || "general";

  // IMPORTANT: preserve the file naming contract `round-{n}-{type}-...`
  // If we couldn't parse a number, keep `0` but make the type unique to avoid overwrites.
  const fileRoundNumber = roundNumber ?? 0;
  const canonical = `${fileRoundNumber}-${roundType}`;
  return { roundNumber, roundType, canonical };
}

function strictMarkdownDoc(params: {
  candidateName: string;
  round: string;
  interviewer: string;
  dateISO: string;
  feedback: string;
  strengths?: string;
  weaknesses?: string;
  verdict?: string;
}): string {
  const candidateName = normalizeCandidateName(params.candidateName);
  const round = (params.round ?? "").trim();
  const interviewer = (params.interviewer ?? "").trim();
  const dateISO = params.dateISO;
  const feedback = (params.feedback ?? "").trim();
  const strengths = (params.strengths ?? "").trim() || "_(none)_";
  const weaknesses = (params.weaknesses ?? "").trim() || "_(none)_";
  const verdict = (params.verdict ?? "").trim() || "_(none)_";

  // We must not invent sections; keep blanks if user didn't provide structure.
  // If user wrote labeled sections, we keep them within Summary to avoid guessing.
  const summary = feedback || "";

  return [
    `# Candidate: ${candidateName}`,
    ``,
    `# Round: ${round}`,
    ``,
    `# Interviewer: ${interviewer}`,
    ``,
    `# Date: ${dateISO}`,
    ``,
    `## Summary`,
    ``,
    summary,
    ``,
    `## Strengths`,
    strengths,
    ``,
    `## Weaknesses`,
    weaknesses,
    ``,
    `## Verdict`,
    verdict,
    ``,
    `---`,
    ``,
  ].join("\n");
}

async function ensureDir(p: string) {
  await fs.mkdir(p, { recursive: true });
}

export async function saveFeedback(input: SaveFeedbackInput): Promise<SaveFeedbackResult> {
  const candidateName = normalizeCandidateName(input.candidateName);
  const candidateSlug = slugify(candidateName);
  const interviewerSlug = slugify(input.interviewer);
  const { roundNumber, roundType } = parseRound(input.round);
  const dateISO = new Date().toISOString();

  const candidateDir = path.join(FEEDBACK_DIR, candidateSlug);
  await ensureDir(candidateDir);

  // File spec (user request): candidateName_interviewer_name_round.md
  // We keep `candidateSlug/` directory, but also include candidate/interviewer in the filename.
  // Store round in filename as a number only (e.g. round-3.md).
  // Still include interviewer to avoid overwrites across interviewers.
  const fileRoundNumber = roundNumber ?? 0;
  const fileName = `${candidateSlug}_${interviewerSlug}_round-${fileRoundNumber}.md`;
  const filePath = path.join(candidateDir, fileName);

  const markdown = strictMarkdownDoc({
    candidateName,
    round: input.round,
    interviewer: input.interviewer,
    dateISO,
    feedback: input.feedback,
    strengths: input.strengths,
    weaknesses: input.weaknesses,
    verdict: input.verdict,
  });

  await fs.writeFile(filePath, markdown, "utf8");

  return {
    ok: true,
    candidateName,
    candidateSlug,
    filePath,
    roundNumber,
    roundType,
    interviewerSlug,
    dateISO,
  };
}

async function listCandidateFiles(candidateSlug: string): Promise<string[]> {
  const dir = path.join(FEEDBACK_DIR, candidateSlug);
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    return entries.filter((e) => e.isFile() && e.name.endsWith(".md")).map((e) => path.join(dir, e.name));
  } catch (e: any) {
    if (e?.code === "ENOENT") return [];
    throw e;
  }
}

function extractRoundLabelFromFilename(filePath: string): string | null {
  const base = path.basename(filePath);
  // Old format:
  //   round-{n}-{type}-{interviewer}.md  => canonical: {n}-{type}
  const old = base.match(/^round-([0-9]+)-([a-z0-9-]+)-/i);
  if (old) return String(Number(old[1]));

  // New format:
  //   {candidateSlug}_{interviewerSlug}_round-{n}.md
  const nu = base.match(/_round-([0-9]+)\.md$/i);
  if (!nu) return null;
  return nu[1] ? String(Number(nu[1])) : null;
}

export async function listRounds(candidateName: string): Promise<ListRoundsResult> {
  const candidateSlug = slugify(candidateName);
  const files = await listCandidateFiles(candidateSlug);
  const rounds = Array.from(
    new Set(files.map(extractRoundLabelFromFilename).filter((x): x is string => Boolean(x))),
  ).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  if (files.length === 0) {
    return { ok: false, error: `No feedback found for candidate "${candidateName}".`, rounds: [] };
  }
  return { ok: true, candidateName: normalizeCandidateName(candidateName), candidateSlug, rounds };
}

export async function getFeedback(candidateName: string, round: string): Promise<GetFeedbackResult> {
  const candidateSlug = slugify(candidateName);
  const files = await listCandidateFiles(candidateSlug);
  if (files.length === 0) {
    return { ok: false, error: `No feedback found for candidate "${candidateName}".`, availableRounds: [] };
  }

  const parsed = parseRound(round);
  const wantedRoundNumber = parsed.roundNumber ?? 0;

  const matching = files.filter((fp) => {
    const base = path.basename(fp);

    // New format match
    if (base.includes(`_round-${wantedRoundNumber}.md`)) return true;

    // Old format match (best-effort)
    // old filename: round-{n}-{type}-{interviewer}.md
    const oldPrefix = `round-${wantedRoundNumber}-`;
    if (base.startsWith(oldPrefix)) return true;

    return false;
  });

  const availableRounds = Array.from(
    new Set(files.map(extractRoundLabelFromFilename).filter((x): x is string => Boolean(x))),
  ).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  if (matching.length === 0) {
    return {
      ok: false,
      error: `No feedback found for round "${round}" for candidate "${candidateName}".`,
      availableRounds,
    };
  }

  const payload = await Promise.all(
    matching.map(async (fp) => ({ filePath: fp, markdown: await fs.readFile(fp, "utf8") })),
  );

  return {
    ok: true,
    candidateName: normalizeCandidateName(candidateName),
    candidateSlug,
    round,
    files: payload,
  };
}

