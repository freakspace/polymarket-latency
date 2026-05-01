import { promises as fs, createReadStream } from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { z } from "zod";
import { createInterface } from "node:readline";

const RECORDINGS_ROOT = path.resolve(process.cwd(), "..", "recordings", "ws-bench");

const TimelineIndex = z.object({
  events_log: z.object({
    filename: z.string(),
    byte_offsets: z.array(z.tuple([z.number(), z.number()])),
    stride: z.number(),
  }),
  run_started_ns: z.number(),
  run_ended_ns: z.number(),
});

export async function GET(
  request: Request,
  context: { params: Promise<{ timestamp: string }> },
): Promise<Response> {
  const { timestamp } = await context.params;
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) {
    return NextResponse.json({ error: "invalid timestamp" }, { status: 400 });
  }
  const url = new URL(request.url);
  const connectionId = url.searchParams.get("connection_id");
  const startNs = Number(url.searchParams.get("start_ns") ?? "");
  const endNs = Number(url.searchParams.get("end_ns") ?? "");
  const limit = Math.max(1, Math.min(2000, Number(url.searchParams.get("limit") ?? "500")));
  if (
    !connectionId ||
    !/^[A-Za-z0-9_]+$/.test(connectionId) ||
    !Number.isFinite(startNs) ||
    !Number.isFinite(endNs) ||
    endNs < startNs
  ) {
    return NextResponse.json({ error: "invalid query" }, { status: 400 });
  }

  const runDir = path.join(RECORDINGS_ROOT, timestamp);
  const indexPath = path.join(runDir, "timeline", "index.json");
  let index: z.infer<typeof TimelineIndex>;
  try {
    const raw = await fs.readFile(indexPath, "utf8");
    index = TimelineIndex.parse(JSON.parse(raw));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return NextResponse.json({ error: "no timeline index" }, { status: 404 });
    }
    throw err;
  }

  const eventsPath = path.join(runDir, index.events_log.filename);
  const eventsStat = await fs.stat(eventsPath).catch(() => null);
  if (!eventsStat) {
    return NextResponse.json({ error: "events.jsonl missing" }, { status: 404 });
  }

  const offsets = index.events_log.byte_offsets;
  // Binary-search for the largest offset whose received_at_ns <= startNs
  // (so we never start past the window). Fall back to 0.
  let lo = 0;
  let hi = offsets.length - 1;
  let pos = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const tsAtMid = offsets[mid][1];
    if (tsAtMid <= startNs) {
      pos = offsets[mid][0];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  const events: unknown[] = [];
  let truncated = false;

  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(eventsPath, { start: pos, encoding: "utf8" });
    const rl = createInterface({ input: stream, crlfDelay: Infinity });
    rl.on("line", (line: string) => {
      if (events.length >= limit) {
        truncated = true;
        rl.close();
        return;
      }
      if (!line) return;
      let rec: Record<string, unknown>;
      try {
        rec = JSON.parse(line);
      } catch {
        return;
      }
      const ts = rec.received_at_ns;
      const cid = rec.connection_id;
      if (typeof ts !== "number") return;
      if (ts > endNs) {
        // The events stream is *roughly* monotonic: workers race a little, but
        // strictly past `endNs + retention` we can be confident the rest are
        // also too late. Use a small slack to be safe.
        if (ts > endNs + 60_000_000_000) {
          rl.close();
        }
        return;
      }
      if (ts < startNs) return;
      if (cid !== connectionId) return;
      events.push(rec);
    });
    rl.on("close", () => resolve());
    rl.on("error", reject);
    stream.on("error", reject);
  });

  return NextResponse.json({
    connection_id: connectionId,
    start_ns: startNs,
    end_ns: endNs,
    truncated,
    events,
  });
}

export const dynamic = "force-dynamic";
