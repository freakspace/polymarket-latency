import { promises as fs } from "node:fs";
import path from "node:path";
import { parseSummary, type Summary } from "./summary-schema";

const RECORDINGS_ROOT = path.resolve(process.cwd(), "..", "recordings", "ws-bench");

export type RunListing = {
  timestamp: string;
  path: string;
  summaryPath: string;
  hasEvents: boolean;
  summary: Summary;
};

export async function listRuns(): Promise<RunListing[]> {
  let entries: string[];
  try {
    entries = await fs.readdir(RECORDINGS_ROOT);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
  const runs: RunListing[] = [];
  for (const entry of entries) {
    const runDir = path.join(RECORDINGS_ROOT, entry);
    const stat = await fs.stat(runDir).catch(() => null);
    if (!stat?.isDirectory()) continue;
    const summaryPath = path.join(runDir, "summary.json");
    let raw: string;
    try {
      raw = await fs.readFile(summaryPath, "utf8");
    } catch {
      continue;
    }
    try {
      const summary = parseSummary(JSON.parse(raw));
      const hasEvents = await fs
        .access(path.join(runDir, "events.jsonl"))
        .then(() => true)
        .catch(() => false);
      runs.push({
        timestamp: entry,
        path: runDir,
        summaryPath,
        hasEvents,
        summary,
      });
    } catch (err) {
      console.warn(`[recordings] skipping ${entry}:`, err);
    }
  }
  runs.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  return runs;
}

export async function loadRun(timestamp: string): Promise<RunListing | null> {
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) return null;
  const runDir = path.join(RECORDINGS_ROOT, timestamp);
  const summaryPath = path.join(runDir, "summary.json");
  let raw: string;
  try {
    raw = await fs.readFile(summaryPath, "utf8");
  } catch {
    return null;
  }
  const summary = parseSummary(JSON.parse(raw));
  const hasEvents = await fs
    .access(path.join(runDir, "events.jsonl"))
    .then(() => true)
    .catch(() => false);
  return {
    timestamp,
    path: runDir,
    summaryPath,
    hasEvents,
    summary,
  };
}

export function recordingsRoot(): string {
  return RECORDINGS_ROOT;
}
