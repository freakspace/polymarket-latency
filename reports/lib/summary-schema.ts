import { z } from "zod";

const nn = z.number().nullable().optional();

const distribution = z
  .object({
    approximate: z.boolean().optional(),
    count: z.number(),
    max: nn,
    mean: nn,
    min: nn,
    p50: nn,
    p95: nn,
    p99: nn,
    sample_count: z.number().optional(),
  })
  .passthrough();

const histogram = z
  .object({
    approximate: z.boolean().optional(),
    bin_count: z.number().optional(),
    bins: z.array(
      z.object({
        count: z.number(),
        end: z.number(),
        start: z.number(),
      })
    ),
    count: z.number().optional(),
    max: z.number().optional(),
    min: z.number().optional(),
    sample_count: z.number().optional(),
  })
  .passthrough();

const topologyStats = z
  .object({
    topology_size: z.number(),
    connection_ids: z.array(z.string()),
    coverage_rate: z.number(),
    first_seen_win_rate: z.number(),
    first_seen_wins: z.number().optional(),
    seen_event_count: z.number().optional(),
    event_observations: z.number().optional(),
    duplicate_observations: z.number().optional(),
    intra_topology_dup_rate: z.number().optional(),
    relative_miss_rate: z.number(),
    relative_loss_count: z.number().optional(),
    relative_gap_runs: z.number(),
    relative_gap_events_total: z.number(),
    largest_relative_gap_ms: z.number().nullable(),
    largest_relative_gap_events: z.number().nullable(),
    longest_inter_event_gap_ms: nn,
    arrival_delta_ms: distribution,
    freshness_ms: distribution,
    inter_event_gap_ms: distribution.optional(),
    relative_gap_duration_ms: distribution.optional(),
    relative_gap_events: distribution.optional(),
    arrival_delta_histogram_ms: histogram.optional(),
    freshness_histogram_ms: histogram.optional(),
    largest_relative_gap: z.unknown().optional(),
  })
  .passthrough();

const largestGapDetail = z
  .object({
    duration_ms: z.number(),
    events: z.number(),
    started_at: z.string(),
    ended_at: z.string(),
    start_event_key: z.string().optional(),
    end_event_key: z.string().optional(),
  })
  .passthrough();

const connectionStats = z
  .object({
    connection_id: z.string(),
    topology_id: z.string(),
    topology_size: z.number(),
    connected: z.boolean().optional(),
    connection_attempts: z.number().optional(),
    successful_connects: z.number().optional(),
    connect_failures: z.number().optional(),
    reconnects: z.number().optional(),
    disconnects: z.number().optional(),
    market_rebinds: z.number().optional(),
    malformed_messages: z.number().optional(),
    control_messages: z.number().optional(),
    filtered_messages: z.number().optional(),
    total_events: z.number().optional(),
    total_messages: z.number().optional(),
    seen_event_count: z.number().optional(),
    scored_event_observations: z.number().optional(),
    coverage_rate: z.number(),
    first_seen_wins: z.number().optional(),
    first_seen_win_rate: z.number(),
    duplicate_observations: z.number().optional(),
    intra_connection_dup_rate: z.number().optional(),
    relative_miss_rate: z.number(),
    relative_loss_count: z.number().optional(),
    relative_gap_runs: z.number().optional(),
    relative_gap_events_total: z.number().optional(),
    largest_relative_gap_ms: nn,
    largest_relative_gap_events: nn,
    longest_silence_seconds: nn,
    longest_inter_event_gap_ms: nn,
    current_silence_seconds: nn,
    current_market_slug: z.string().optional(),
    current_segment_id: z.string().optional(),
    current_series_id: z.string().optional(),
    switch_reason: z.string().nullable().optional(),
    last_error: z.string().nullable().optional(),
    in_warmup: z.boolean().optional(),
    warmup_remaining_seconds: z.number().optional(),
    warmup_resets: z.number().optional(),
    arrival_delta_ms: distribution,
    freshness_ms: distribution,
    inter_event_gap_ms: distribution.optional(),
    relative_gap_duration_ms: distribution.optional(),
    relative_gap_events: distribution.optional(),
    arrival_delta_histogram_ms: histogram.optional(),
    freshness_histogram_ms: histogram.optional(),
    largest_relative_gap: largestGapDetail.nullable().optional(),
  })
  .passthrough();

const warmupComparisonEntry = z
  .object({
    supports_warmup: z.boolean(),
    warmup_arrival_p95_ms: z.number(),
    post_warmup_arrival_p95_ms: z.number(),
    arrival_p95_delta_ms: z.number(),
    warmup_freshness_p95_ms: z.number(),
    post_warmup_freshness_p95_ms: z.number(),
    freshness_p95_delta_ms: z.number(),
    warmup_duplicate_rate: z.number().optional(),
    post_warmup_duplicate_rate: z.number().optional(),
    duplicate_rate_delta: z.number().optional(),
    warmup_observations: z.number().optional(),
    post_warmup_observations: z.number().optional(),
    warmup_topology_seen_event_count: z.number().optional(),
    post_warmup_topology_seen_event_count: z.number().optional(),
  })
  .passthrough();

export const summarySchema = z
  .object({
    run_metadata: z
      .object({
        series_id: z.string(),
        market_slug: z.string().optional(),
        observed_market_slugs: z.array(z.string()).optional(),
        endpoint: z.string(),
        duration_seconds: z.number(),
        topologies: z.array(z.number()),
        warmup_seconds: z.number().optional(),
        warmup_compare_window_seconds: z.number().optional(),
        event_types: z.array(z.string()).optional(),
        started_at: z.string(),
        ended_at: z.string(),
        output_dir: z.string().optional(),
        token_ids: z.array(z.string()).optional(),
      })
      .passthrough(),
    topologies: z.record(z.string(), topologyStats),
    connections: z.record(z.string(), connectionStats),
    comparative_insights: z
      .object({
        baseline_topology: z.number(),
        best_coverage_topology: z.number(),
        best_first_seen_topology: z.number(),
        lowest_relative_miss_topology: z.number(),
        lowest_gap_topology: z.number(),
        largest_topology: z.number(),
        coverage_gain_vs_baseline: z.number(),
        first_seen_gain_vs_baseline: z.number(),
        relative_miss_reduction_vs_baseline: z.number(),
        largest_relative_gap_ms_reduction_vs_baseline: z.number(),
        largest_relative_gap_events_reduction_vs_baseline: z.number(),
      })
      .passthrough(),
    warmup_evidence: z
      .object({
        warmup_phase: z.unknown().optional(),
        post_warmup_compare_phase: z.unknown().optional(),
        compare_window_seconds: z.number().optional(),
        topology_comparison: z.record(z.string(), warmupComparisonEntry),
      })
      .passthrough(),
    activity_window: z
      .object({
        first_scored_event_at: z.string().optional(),
        first_scored_event_offset_seconds: z.number().optional(),
        last_scored_event_at: z.string().optional(),
        scored_event_span_seconds: z.number().optional(),
        run_tail_silence_seconds: z.number().optional(),
        longest_union_inter_event_gap_ms: z.number().optional(),
        union_inter_event_gap_ms: distribution.optional(),
      })
      .passthrough()
      .optional(),
    market_segments: z
      .array(
        z
          .object({
            segment_id: z.string(),
            series_id: z.string().optional(),
            market_slug: z.string(),
            started_at_iso: z.string(),
            ended_at_iso: z.string(),
            started_at_ns: z.number().optional(),
            ended_at_ns: z.number().optional(),
            switch_reason: z.string().optional(),
            token_ids: z.array(z.string()).optional(),
          })
          .passthrough()
      )
      .optional(),
    caveats: z.array(z.string()).optional(),
    timestamp_parseability: z.unknown().optional(),
    all_observations: z.number().optional(),
    scored_observations: z.number().optional(),
    scored_union_event_count: z.number().optional(),
  })
  .passthrough();

export type Summary = z.infer<typeof summarySchema>;
export type TopologyStats = z.infer<typeof topologyStats>;
export type ConnectionStats = z.infer<typeof connectionStats>;
export type Distribution = z.infer<typeof distribution>;
export type Histogram = z.infer<typeof histogram>;
export type WarmupComparisonEntry = z.infer<typeof warmupComparisonEntry>;

export function parseSummary(raw: unknown): Summary {
  return summarySchema.parse(raw);
}
