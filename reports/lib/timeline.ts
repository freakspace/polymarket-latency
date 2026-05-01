import { z } from "zod";

const BUCKET_ROW = z.tuple([
  z.number(), // bucket_idx (relative)
  z.number(), // msg_count
  z.number().nullable(), // arrival_delta_p50_ms
  z.number().nullable(), // arrival_delta_p95_ms
  z.number().nullable(), // freshness_p95_ms
  z.number(), // in_warmup (0 / 1)
]);
export type BucketRow = z.infer<typeof BUCKET_ROW>;

const CONNECTION_META = z.object({
  connection_id: z.string(),
  topology_id: z.string(),
  topology_size: z.number().nullable().optional(),
});
export type ConnectionMeta = z.infer<typeof CONNECTION_META>;

export const TimelineIndexSchema = z.object({
  schema_version: z.number(),
  run_started_ns: z.number(),
  run_ended_ns: z.number(),
  duration_seconds: z.number(),
  connections: z.array(CONNECTION_META),
  topologies: z.array(z.string()),
  event_retention_seconds: z.number(),
  events_log: z.object({
    filename: z.string(),
    line_count: z.number(),
    skipped_lines: z.number(),
    byte_offsets: z.array(z.tuple([z.number(), z.number()])),
    stride: z.number(),
  }),
  bucket_widths_seconds: z.array(z.number()),
  gap_threshold_seconds: z.number(),
  generated_at: z.number(),
});
export type TimelineIndex = z.infer<typeof TimelineIndexSchema>;

export const BucketsOverviewSchema = z.object({
  bucket_size_ns: z.number(),
  first_bucket_start_ns: z.number(),
  row_schema: z.array(z.string()),
  rows_by_connection: z.record(z.string(), z.array(BUCKET_ROW)),
});
export type BucketsOverview = z.infer<typeof BucketsOverviewSchema>;

export const BucketsShardSchema = z.object({
  connection_id: z.string(),
  bucket_size_ns: z.number(),
  first_bucket_start_ns: z.number(),
  row_schema: z.array(z.string()),
  rows: z.array(BUCKET_ROW),
});
export type BucketsShard = z.infer<typeof BucketsShardSchema>;

const TRANSITION = z.object({
  connection_id: z.string(),
  kind: z.enum([
    "reconnect",
    "disconnect",
    "connect_failure",
    "market_rebind",
    "rotation",
    "warmup_reset",
    "error",
  ]),
  at_ns: z.number(),
  delta: z.number().optional(),
  context: z.record(z.string(), z.unknown()).optional(),
});
export type Transition = z.infer<typeof TRANSITION>;

export const TransitionsFileSchema = z.object({
  transitions: z.array(TRANSITION),
});

const GAP = z.object({
  connection_id: z.string(),
  start_ns: z.number(),
  end_ns: z.number(),
  duration_seconds: z.number(),
  kind: z.string(),
});
export type Gap = z.infer<typeof GAP>;

export const GapsFileSchema = z.object({
  gap_threshold_seconds: z.number(),
  gaps: z.array(GAP),
});

export type TimelineBundle = {
  index: TimelineIndex;
  overview: BucketsOverview;
  transitions: Transition[];
  gaps: Gap[];
};

/**
 * Color encoding for the swimlane heatmap.
 *
 * Default: arrival_delta_p95_ms vs the topology's fastest-at-this-moment leader.
 * Green = within 50ms, yellow = 50-200ms behind, red = 200ms-2s, dark red = >2s.
 */
export function arrivalDeltaColor(p95Ms: number | null): string {
  if (p95Ms === null) return "#1f2937"; // empty bucket
  const m = Math.max(0, p95Ms);
  if (m <= 5) return "#16a34a"; // ~leader
  if (m <= 50) return "#65a30d";
  if (m <= 200) return "#ca8a04";
  if (m <= 1000) return "#dc2626";
  if (m <= 2000) return "#b91c1c";
  return "#7f1d1d"; // >2s, effectively dead
}

export function freshnessColor(p95Ms: number | null): string {
  if (p95Ms === null) return "#1f2937";
  const m = Math.max(0, p95Ms);
  if (m <= 100) return "#16a34a";
  if (m <= 250) return "#65a30d";
  if (m <= 500) return "#ca8a04";
  if (m <= 1000) return "#dc2626";
  if (m <= 5000) return "#b91c1c";
  return "#7f1d1d";
}

export function msgRateColor(count: number, bucketSeconds: number): string {
  const rate = count / bucketSeconds;
  if (count === 0) return "#1f2937";
  if (rate < 1) return "#475569";
  if (rate < 5) return "#0369a1";
  if (rate < 20) return "#0284c7";
  if (rate < 50) return "#0ea5e9";
  return "#38bdf8";
}

export type ColorMode = "arrival_delta" | "freshness" | "msg_rate";

export function colorFor(
  row: BucketRow,
  mode: ColorMode,
  bucketSeconds: number,
): string {
  switch (mode) {
    case "arrival_delta":
      return arrivalDeltaColor(row[3]);
    case "freshness":
      return freshnessColor(row[4]);
    case "msg_rate":
      return msgRateColor(row[1], bucketSeconds);
  }
}
