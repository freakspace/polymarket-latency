import { promises as fs } from "node:fs";
import path from "node:path";
import {
  BucketsOverviewSchema,
  BucketsShardSchema,
  GapsFileSchema,
  TimelineIndexSchema,
  TransitionsFileSchema,
  type BucketsShard,
  type TimelineBundle,
} from "./timeline";

const RECORDINGS_ROOT = path.resolve(process.cwd(), "..", "recordings", "ws-bench");

function timelineDir(timestamp: string): string {
  return path.join(RECORDINGS_ROOT, timestamp, "timeline");
}

export async function hasTimeline(timestamp: string): Promise<boolean> {
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) return false;
  return fs
    .access(path.join(timelineDir(timestamp), "index.json"))
    .then(() => true)
    .catch(() => false);
}

async function readJson<T>(
  p: string,
  schema: { parse: (input: unknown) => T },
): Promise<T> {
  const raw = await fs.readFile(p, "utf8");
  return schema.parse(JSON.parse(raw));
}

export async function loadTimelineBundle(
  timestamp: string,
): Promise<TimelineBundle | null> {
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) return null;
  const dir = timelineDir(timestamp);
  try {
    const [index, overview, transitions, gaps] = await Promise.all([
      readJson(path.join(dir, "index.json"), TimelineIndexSchema),
      readJson(path.join(dir, "buckets_60s.json"), BucketsOverviewSchema),
      readJson(path.join(dir, "transitions.json"), TransitionsFileSchema),
      readJson(path.join(dir, "gaps.json"), GapsFileSchema),
    ]);
    return {
      index,
      overview,
      transitions: transitions.transitions,
      gaps: gaps.gaps,
    };
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
}

export async function loadShard(
  timestamp: string,
  connectionId: string,
): Promise<BucketsShard | null> {
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) return null;
  if (!/^[A-Za-z0-9_]+$/.test(connectionId)) return null;
  const p = path.join(timelineDir(timestamp), "conn", `${connectionId}.10s.json`);
  try {
    return await readJson(p, BucketsShardSchema);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
}
