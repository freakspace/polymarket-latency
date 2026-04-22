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
    client_success_count: z.number().optional(),
    duplicate_reject_count: z.number().optional(),
    fanout: z.number(),
    landed_without_success_response: z.boolean().optional(),
    new_open_order_count: z.number(),
    new_open_order_ids: z.array(z.string()).default([]),
    requests: z.array(requestSchema),
    shared_timestamp_ms: z.number(),
    summary: fanoutSummarySchema,
    cleanup_result: z.unknown().optional(),
    transport_error_count: z.number().optional(),
    winner_landed: z.boolean().optional(),
  })
  .passthrough();

const repeatRunSchema = z
  .object({
    baseline_fastest_success_ms: z.number().nullable().optional(),
    repeat_index: z.number(),
    results: z.array(fanoutResultSchema).default([]),
  })
  .passthrough();

const aggregateSampleSchema = z
  .object({
    sample_count: z.number(),
    min: z.number().nullable(),
    median: z.number().nullable(),
    max: z.number().nullable(),
  })
  .passthrough();

const aggregateFanoutSchema = z
  .object({
    beat_repeat_baseline_count: z.number(),
    beat_repeat_baseline_rate: z.number(),
    client_success_rate: z.number(),
    client_success_repeat_count: z.number(),
    comparable_repeat_count: z.number(),
    duplicate_reject_total: z.number(),
    fanout: z.number(),
    improvement_vs_repeat_baseline_ms: aggregateSampleSchema,
    landed_without_success_response_count: z.number(),
    landed_without_success_response_rate: z.number(),
    observed_winner_latency_ms: aggregateSampleSchema,
    orders_landed_total: z.number(),
    repeat_count: z.number(),
    transport_error_total: z.number(),
    winner_landed_count: z.number(),
    winner_landed_rate: z.number(),
  })
  .passthrough();

const orderBurstSchema = z
  .object({
    aggregate_by_fanout: z.array(aggregateFanoutSchema).optional().default([]),
    chain_id: z.number(),
    cleanup: z.boolean(),
    counts: z.array(z.number()),
    host: z.string(),
    post_only: z.boolean(),
    price: z.string(),
    resolved_chain_id: z.number(),
    repeat_runs: z.array(repeatRunSchema).optional().default([]),
    repeats: z.number().optional().default(1),
    results: z.array(fanoutResultSchema).optional().default([]),
    side: z.string(),
    size: z.string(),
    token_id: z.string(),
    balance_allowance_preflight: z.unknown().optional(),
  })
  .passthrough();

export type OrderBurstSummary = z.infer<typeof orderBurstSchema>;
export type OrderBurstFanout = z.infer<typeof fanoutResultSchema>;
export type OrderBurstRequest = z.infer<typeof requestSchema>;
export type OrderBurstRepeatRun = z.infer<typeof repeatRunSchema>;
export type OrderBurstAggregateFanout = z.infer<typeof aggregateFanoutSchema>;

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
  winnerLanded: boolean;
  successRate: number;
  successCount: number;
  totalCount: number;
  duplicateRejectCount: number;
  transportErrorCount: number;
  landedWithoutSuccessResponse: boolean;
  fastestMs: number | null;
  medianMs: number;
  minMs: number;
  maxMs: number;
  improvementVsOneMs: number | null;
};

export function repeatRuns(summary: OrderBurstSummary): OrderBurstRepeatRun[] {
  if (summary.repeat_runs.length > 0) return summary.repeat_runs;
  return [
    {
      repeat_index: 1,
      baseline_fastest_success_ms:
        summary.results.find((r) => r.fanout === 1)?.summary.fastest_success_ms ??
        null,
      results: summary.results,
    },
  ];
}

export function aggregateByFanout(
  summary: OrderBurstSummary
): OrderBurstAggregateFanout[] {
  if (summary.aggregate_by_fanout.length > 0) return summary.aggregate_by_fanout;

  const repeatRun = repeatRuns(summary)[0];
  return summary.results.map((r) => {
    const clientSuccessCount =
      r.client_success_count ??
      r.requests.filter((req) => req.kind === "success").length;
    const duplicateRejectTotal =
      r.duplicate_reject_count ??
      r.requests.filter((req) => req.kind === "duplicate").length;
    const transportErrorTotal =
      r.transport_error_count ??
      r.requests.filter((req) => req.kind === "transport_error").length;
    const winnerLanded = r.winner_landed ?? r.new_open_order_count > 0;
    const landedWithoutSuccess =
      r.landed_without_success_response ??
      (r.new_open_order_count > 0 && clientSuccessCount < r.new_open_order_count);
    const baseline = repeatRun.baseline_fastest_success_ms ?? null;
    const fastest = r.summary.fastest_success_ms ?? null;
    const comparable = baseline != null && fastest != null;
    const improvement = comparable ? baseline - fastest : null;
    return {
      fanout: r.fanout,
      repeat_count: 1,
      winner_landed_count: winnerLanded ? 1 : 0,
      winner_landed_rate: winnerLanded ? 1 : 0,
      client_success_repeat_count: clientSuccessCount > 0 ? 1 : 0,
      client_success_rate: clientSuccessCount > 0 ? 1 : 0,
      landed_without_success_response_count: landedWithoutSuccess ? 1 : 0,
      landed_without_success_response_rate: landedWithoutSuccess ? 1 : 0,
      duplicate_reject_total: duplicateRejectTotal,
      transport_error_total: transportErrorTotal,
      orders_landed_total: r.new_open_order_count,
      observed_winner_latency_ms: {
        sample_count: fastest != null ? 1 : 0,
        min: fastest,
        median: fastest,
        max: fastest,
      },
      improvement_vs_repeat_baseline_ms: {
        sample_count: improvement != null ? 1 : 0,
        min: improvement,
        median: improvement,
        max: improvement,
      },
      comparable_repeat_count: comparable ? 1 : 0,
      beat_repeat_baseline_count: improvement != null && improvement > 0 ? 1 : 0,
      beat_repeat_baseline_rate: improvement != null && improvement > 0 ? 1 : 0,
    };
  });
}

export function headlineByFanout(summary: OrderBurstSummary): BurstHeadline[] {
  const aggregate = aggregateByFanout(summary);
  return aggregate.map((row) => {
    return {
      fanout: row.fanout,
      winnerLanded: row.winner_landed_count > 0,
      successRate: row.client_success_rate,
      successCount: row.client_success_repeat_count,
      totalCount: row.repeat_count,
      duplicateRejectCount: row.duplicate_reject_total,
      transportErrorCount: row.transport_error_total,
      landedWithoutSuccessResponse:
        row.landed_without_success_response_count > 0,
      fastestMs: row.observed_winner_latency_ms.median,
      medianMs: row.observed_winner_latency_ms.median ?? 0,
      minMs: row.observed_winner_latency_ms.min ?? 0,
      maxMs: row.observed_winner_latency_ms.max ?? 0,
      improvementVsOneMs: row.improvement_vs_repeat_baseline_ms.median,
    };
  });
}
