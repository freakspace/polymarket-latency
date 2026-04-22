import { promises as fs } from "node:fs";
import path from "node:path";
import { z } from "zod";

const ORDER_BURST_ROOT = path.resolve(
  process.cwd(),
  "..",
  "recordings",
  "order-burst"
);

const requestSchema = z
  .object({
    error: z.string().optional().default(""),
    index: z.number(),
    kind: z.string(),
    latency_ms: z.number(),
    order_id: z.string().nullable().optional(),
    salt: z.string().optional(),
    status: z.string().nullable().optional(),
    success: z.boolean().optional(),
    timestamp_ms: z.number(),
  })
  .passthrough();

const fanoutSummarySchema = z
  .object({
    fastest_success_ms: z.number().nullable(),
    latency_ms: z.object({
      min: z.number(),
      median: z.number(),
      max: z.number(),
    }),
    request_counts: z.record(z.string(), z.number()),
  })
  .passthrough();

const fanoutResultSchema = z
  .object({
    fanout: z.number(),
    new_open_order_count: z.number(),
    new_open_order_ids: z.array(z.string()).default([]),
    requests: z.array(requestSchema),
    shared_timestamp_ms: z.number(),
    summary: fanoutSummarySchema,
    cleanup_result: z.unknown().optional(),
  })
  .passthrough();

const orderBurstSchema = z
  .object({
    chain_id: z.number(),
    cleanup: z.boolean(),
    counts: z.array(z.number()),
    host: z.string(),
    post_only: z.boolean(),
    price: z.string(),
    resolved_chain_id: z.number(),
    results: z.array(fanoutResultSchema),
    side: z.string(),
    size: z.string(),
    token_id: z.string(),
    balance_allowance_preflight: z.unknown().optional(),
  })
  .passthrough();

export type OrderBurstSummary = z.infer<typeof orderBurstSchema>;
export type OrderBurstFanout = z.infer<typeof fanoutResultSchema>;
export type OrderBurstRequest = z.infer<typeof requestSchema>;

export type OrderBurstListing = {
  timestamp: string;
  path: string;
  summaryPath: string;
  summary: OrderBurstSummary;
};

export function parseOrderBurstSummary(value: unknown): OrderBurstSummary {
  return orderBurstSchema.parse(value);
}

export function orderBurstRoot(): string {
  return ORDER_BURST_ROOT;
}

export async function listOrderBursts(): Promise<OrderBurstListing[]> {
  let entries: string[];
  try {
    entries = await fs.readdir(ORDER_BURST_ROOT);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
  const runs: OrderBurstListing[] = [];
  for (const entry of entries) {
    const runDir = path.join(ORDER_BURST_ROOT, entry);
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
      const summary = parseOrderBurstSummary(JSON.parse(raw));
      runs.push({ timestamp: entry, path: runDir, summaryPath, summary });
    } catch (err) {
      console.warn(`[order-burst] skipping ${entry}:`, err);
    }
  }
  runs.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  return runs;
}

export async function loadOrderBurst(
  timestamp: string
): Promise<OrderBurstListing | null> {
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) return null;
  const runDir = path.join(ORDER_BURST_ROOT, timestamp);
  const summaryPath = path.join(runDir, "summary.json");
  let raw: string;
  try {
    raw = await fs.readFile(summaryPath, "utf8");
  } catch {
    return null;
  }
  const summary = parseOrderBurstSummary(JSON.parse(raw));
  return { timestamp, path: runDir, summaryPath, summary };
}

export type BurstHeadline = {
  fanout: number;
  successRate: number;
  successCount: number;
  totalCount: number;
  fastestMs: number | null;
  medianMs: number;
  minMs: number;
  maxMs: number;
  improvementVsOneMs: number | null;
};

export function headlineByFanout(summary: OrderBurstSummary): BurstHeadline[] {
  const fanout1Fastest =
    summary.results.find((r) => r.fanout === 1)?.summary.fastest_success_ms ??
    null;
  return summary.results.map((r) => {
    const total = r.requests.length;
    const success = r.requests.filter((req) => req.kind === "success").length;
    const fastest = r.summary.fastest_success_ms;
    const improvement =
      fanout1Fastest != null && fastest != null
        ? fanout1Fastest - fastest
        : null;
    return {
      fanout: r.fanout,
      successRate: total === 0 ? 0 : success / total,
      successCount: success,
      totalCount: total,
      fastestMs: fastest,
      medianMs: r.summary.latency_ms.median,
      minMs: r.summary.latency_ms.min,
      maxMs: r.summary.latency_ms.max,
      improvementVsOneMs: improvement,
    };
  });
}
