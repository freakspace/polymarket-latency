import type {
  ConnectionStats,
  Distribution,
  Histogram,
  Summary,
  TopologyStats,
  WarmupComparisonEntry,
} from "./summary-schema";
import { METRIC_COLORS, topologyColor } from "./palette";

export type Series = {
  label: string;
  color: string;
  values: number[];
};

export type ChartPayload = {
  categories: string[];
  series: Series[];
  valueKind: "percent" | "ms" | "count";
  title: string;
  subtitle?: string;
};

export type Direction = "low" | "high";

function sortedTopologyEntries(summary: Summary): Array<[number, TopologyStats]> {
  return Object.entries(summary.topologies)
    .map(([k, v]) => [Number(k), v] as [number, TopologyStats])
    .sort((a, b) => a[0] - b[0]);
}

function topologyCategories(summary: Summary): string[] {
  return sortedTopologyEntries(summary).map(([size]) => `${size} ws`);
}

/**
 * Rate [0..1] → percentage points in display space. We multiply here so the
 * raw topology blocks (coverage_rate, first_seen_win_rate, relative_miss_rate)
 * map cleanly onto a "percent" axis.
 */
const toPct = (v: number) => Number((v * 100).toFixed(4));
const roundMs = (v: number | null | undefined): number =>
  v === undefined || v === null ? 0 : Number(v.toFixed(3));

export function topologyPerformanceSeries(summary: Summary): ChartPayload {
  const entries = sortedTopologyEntries(summary);
  return {
    title: "Topology Performance",
    subtitle:
      "Coverage, first-seen share, and relative miss as the websocket pool scales.",
    valueKind: "percent",
    categories: entries.map(([size]) => `${size} ws`),
    series: [
      {
        label: "Coverage",
        color: METRIC_COLORS.coverage,
        values: entries.map(([, t]) => toPct(t.coverage_rate)),
      },
      {
        label: "First Seen",
        color: METRIC_COLORS.firstSeen,
        values: entries.map(([, t]) => toPct(t.first_seen_win_rate)),
      },
      {
        label: "Relative Miss",
        color: METRIC_COLORS.miss,
        values: entries.map(([, t]) => toPct(t.relative_miss_rate)),
      },
    ],
  };
}

export function topologyLatencySeries(summary: Summary): ChartPayload {
  const entries = sortedTopologyEntries(summary);
  return {
    title: "Latency & Freshness",
    subtitle:
      "Lower is better. Shows which topology gets early copies and which one drifts stale.",
    valueKind: "ms",
    categories: entries.map(([size]) => `${size} ws`),
    series: [
      {
        label: "Arrival p50",
        color: METRIC_COLORS.arrivalP50,
        values: entries.map(([, t]) => roundMs(t.arrival_delta_ms.p50)),
      },
      {
        label: "Arrival p95",
        color: METRIC_COLORS.arrivalP95,
        values: entries.map(([, t]) => roundMs(t.arrival_delta_ms.p95)),
      },
      {
        label: "Freshness p50",
        color: METRIC_COLORS.freshnessP50,
        values: entries.map(([, t]) => roundMs(t.freshness_ms.p50)),
      },
      {
        label: "Freshness p95",
        color: METRIC_COLORS.freshnessP95,
        values: entries.map(([, t]) => roundMs(t.freshness_ms.p95)),
      },
    ],
  };
}

export function topologyGapCountsSeries(summary: Summary): ChartPayload {
  const entries = sortedTopologyEntries(summary);
  return {
    title: "Relative Gap Counts",
    subtitle:
      "Topology-relative gap runs (not authoritative venue loss): gap runs, worst gap size, total loss.",
    valueKind: "count",
    categories: entries.map(([size]) => `${size} ws`),
    series: [
      {
        label: "Gap Runs",
        color: METRIC_COLORS.gapRuns,
        values: entries.map(([, t]) => t.relative_gap_runs),
      },
      {
        label: "Largest Gap (events)",
        color: METRIC_COLORS.largestGap,
        values: entries.map(([, t]) => t.largest_relative_gap_events ?? 0),
      },
      {
        label: "Relative Loss",
        color: METRIC_COLORS.relLoss,
        values: entries.map(([, t]) => t.relative_loss_count ?? 0),
      },
    ],
  };
}

export function topologyGapDurationSeries(summary: Summary): ChartPayload {
  const entries = sortedTopologyEntries(summary);
  return {
    title: "Relative Gap Durations",
    subtitle:
      "Longer means the topology went absent while other sockets were still seeing events.",
    valueKind: "ms",
    categories: entries.map(([size]) => `${size} ws`),
    series: [
      {
        label: "Largest Gap",
        color: METRIC_COLORS.gapMs,
        values: entries.map(([, t]) => roundMs(t.largest_relative_gap_ms)),
      },
      {
        label: "Gap Duration p95",
        color: METRIC_COLORS.gapDurP95,
        values: entries.map(([, t]) =>
          roundMs(t.relative_gap_duration_ms?.p95)
        ),
      },
      {
        label: "Inter-Event Gap p95",
        color: METRIC_COLORS.interEventP95,
        values: entries.map(([, t]) => roundMs(t.inter_event_gap_ms?.p95)),
      },
    ],
  };
}

export function warmupDeltaSeries(summary: Summary): ChartPayload {
  const comparison = summary.warmup_evidence.topology_comparison;
  const entries = Object.entries(comparison)
    .map(([k, v]) => [Number(k), v] as [number, WarmupComparisonEntry])
    .sort((a, b) => a[0] - b[0]);
  return {
    title: "Warmup Quality",
    subtitle:
      "Freshness p95 during warmup vs the first stable window after each connect.",
    valueKind: "ms",
    categories: entries.map(([size]) => `${size} ws`),
    series: [
      {
        label: "Warmup p95",
        color: METRIC_COLORS.warmupP95,
        values: entries.map(([, v]) => roundMs(v.warmup_freshness_p95_ms)),
      },
      {
        label: "Post p95",
        color: METRIC_COLORS.postP95,
        values: entries.map(([, v]) => roundMs(v.post_warmup_freshness_p95_ms)),
      },
      {
        label: "Delta",
        color: METRIC_COLORS.delta,
        values: entries.map(([, v]) => roundMs(v.freshness_p95_delta_ms)),
      },
    ],
  };
}

export type ConnectionOutlier = {
  connectionId: string;
  topologySize: number;
  label: string;
  freshnessP95: number;
  arrivalP95: number;
  coverageRate: number;
  disconnects: number;
  reconnects: number;
  color: string;
};

export function connectionOutliers(
  summary: Summary,
  opts: { limit?: number } = {}
): ConnectionOutlier[] {
  const limit = opts.limit ?? 12;
  const conns = Object.values(summary.connections);
  const rows: ConnectionOutlier[] = conns.map((c) => {
    const connNum = c.connection_id.replace(/^topology_\d+_conn_/, "");
    return {
      connectionId: c.connection_id,
      topologySize: c.topology_size,
      label: `${c.topology_size}/${connNum}`,
      freshnessP95: roundMs(c.freshness_ms.p95),
      arrivalP95: roundMs(c.arrival_delta_ms.p95),
      coverageRate: c.coverage_rate,
      disconnects: c.disconnects ?? 0,
      reconnects: c.reconnects ?? 0,
      color: topologyColor(c.topology_size),
    };
  });
  rows.sort((a, b) => b.freshnessP95 - a.freshnessP95);
  return rows.slice(0, limit);
}

export type Verdict = {
  key: "coverage" | "firstSeen" | "miss" | "gap";
  title: string;
  winnerLabel: string;
  body: string;
  chip: "yes" | "mixed" | "no";
};

export function verdicts(summary: Summary): Verdict[] {
  const ci = summary.comparative_insights;
  const { baseline_topology: baseline } = ci;
  return [
    {
      key: "coverage",
      title: "Does a bigger pool cover more events?",
      winnerLabel: `${ci.best_coverage_topology} ws wins`,
      body: `Best coverage at ${ci.best_coverage_topology} ws. Gain vs ${baseline}-ws baseline: ${(ci.coverage_gain_vs_baseline * 100).toFixed(2)} pp.`,
      chip: ci.coverage_gain_vs_baseline > 0 ? "yes" : "no",
    },
    {
      key: "firstSeen",
      title: "Does it get events first?",
      winnerLabel: `${ci.best_first_seen_topology} ws wins`,
      body: `First-seen share peaks at ${ci.best_first_seen_topology} ws; gain vs baseline: ${(ci.first_seen_gain_vs_baseline * 100).toFixed(2)} pp.`,
      chip: ci.first_seen_gain_vs_baseline > 0 ? "yes" : "no",
    },
    {
      key: "miss",
      title: "Does it miss fewer events?",
      winnerLabel: `${ci.lowest_relative_miss_topology} ws wins`,
      body: `Lowest relative miss at ${ci.lowest_relative_miss_topology} ws. Reduction vs baseline: ${(ci.relative_miss_reduction_vs_baseline * 100).toFixed(2)} pp.`,
      chip: ci.relative_miss_reduction_vs_baseline > 0 ? "yes" : "mixed",
    },
    {
      key: "gap",
      title: "Does it shrink the worst gap?",
      winnerLabel: `${ci.lowest_gap_topology} ws wins`,
      body: `Smallest largest-gap at ${ci.lowest_gap_topology} ws. Gap ms reduction vs baseline: ${ci.largest_relative_gap_ms_reduction_vs_baseline.toFixed(1)} ms, events: ${ci.largest_relative_gap_events_reduction_vs_baseline}.`,
      chip:
        ci.largest_relative_gap_ms_reduction_vs_baseline > 0 ? "yes" : "mixed",
    },
  ];
}

export type TableCell = {
  value: number | string;
  display: string;
  isBest: boolean;
};

export type TableMetric = {
  key: string;
  label: string;
  direction: Direction;
  valueKind: "percent" | "ms" | "count";
  get: (t: TopologyStats) => number;
};

export const TOPOLOGY_TABLE_METRICS: TableMetric[] = [
  {
    key: "coverage",
    label: "Coverage",
    direction: "high",
    valueKind: "percent",
    get: (t) => t.coverage_rate,
  },
  {
    key: "firstSeen",
    label: "First Seen",
    direction: "high",
    valueKind: "percent",
    get: (t) => t.first_seen_win_rate,
  },
  {
    key: "miss",
    label: "Rel. Miss",
    direction: "low",
    valueKind: "percent",
    get: (t) => t.relative_miss_rate,
  },
  {
    key: "arrivalP50",
    label: "Arrival p50",
    direction: "low",
    valueKind: "ms",
    get: (t) => t.arrival_delta_ms.p50 ?? 0,
  },
  {
    key: "arrivalP95",
    label: "Arrival p95",
    direction: "low",
    valueKind: "ms",
    get: (t) => t.arrival_delta_ms.p95 ?? 0,
  },
  {
    key: "freshP50",
    label: "Freshness p50",
    direction: "low",
    valueKind: "ms",
    get: (t) => t.freshness_ms.p50 ?? 0,
  },
  {
    key: "freshP95",
    label: "Freshness p95",
    direction: "low",
    valueKind: "ms",
    get: (t) => t.freshness_ms.p95 ?? 0,
  },
  {
    key: "gapRuns",
    label: "Gap Runs",
    direction: "low",
    valueKind: "count",
    get: (t) => t.relative_gap_runs,
  },
  {
    key: "largestGap",
    label: "Largest Gap (ms)",
    direction: "low",
    valueKind: "ms",
    get: (t) => t.largest_relative_gap_ms ?? 0,
  },
  {
    key: "relLoss",
    label: "Relative Loss",
    direction: "low",
    valueKind: "count",
    get: (t) => t.relative_loss_count ?? 0,
  },
];

export type TopologyTableRow = {
  metric: TableMetric;
  cells: Record<number, TableCell>;
};

export function topologyTableRows(summary: Summary): {
  sizes: number[];
  rows: TopologyTableRow[];
} {
  const entries = sortedTopologyEntries(summary);
  const sizes = entries.map(([s]) => s);
  const rows: TopologyTableRow[] = TOPOLOGY_TABLE_METRICS.map((metric) => {
    const values = entries.map(
      ([size, t]) => [size, metric.get(t)] as [number, number]
    );
    const numeric = values.map(([, v]) => v);
    const best =
      metric.direction === "high" ? Math.max(...numeric) : Math.min(...numeric);
    const cells: Record<number, TableCell> = {};
    for (const [size, value] of values) {
      cells[size] = {
        value,
        display: displayForMetric(value, metric.valueKind),
        isBest: value === best,
      };
    }
    return { metric, cells };
  });
  return { sizes, rows };
}

function displayForMetric(
  value: number,
  kind: "percent" | "ms" | "count"
): string {
  if (kind === "percent") return `${(value * 100).toFixed(2)}%`;
  if (kind === "ms") {
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}s`;
    return `${value.toFixed(1)}ms`;
  }
  return value.toLocaleString("en-US");
}

export type Kpi = {
  label: string;
  value: string;
  detail?: string;
  tone: "good" | "accent" | "warn" | "bad" | "neutral";
};

export function headlineKpis(summary: Summary): Kpi[] {
  const ci = summary.comparative_insights;
  const entries = sortedTopologyEntries(summary);

  const byCoverage = [...entries].sort(
    (a, b) => b[1].coverage_rate - a[1].coverage_rate
  )[0];
  const byFreshnessP95 = [...entries].sort(
    (a, b) => (a[1].freshness_ms.p95 ?? Infinity) - (b[1].freshness_ms.p95 ?? Infinity)
  )[0];
  const byArrivalP95 = [...entries].sort(
    (a, b) => (a[1].arrival_delta_ms.p95 ?? Infinity) - (b[1].arrival_delta_ms.p95 ?? Infinity)
  )[0];
  const byLargestGap = [...entries].sort(
    (a, b) =>
      (a[1].largest_relative_gap_ms ?? Infinity) -
      (b[1].largest_relative_gap_ms ?? Infinity)
  )[0];

  return [
    {
      label: "Best Coverage",
      value: `${(byCoverage[1].coverage_rate * 100).toFixed(2)}%`,
      detail: `${byCoverage[0]} ws`,
      tone: "good",
    },
    {
      label: "Best Freshness p95",
      value: displayForMetric(byFreshnessP95[1].freshness_ms.p95 ?? 0, "ms"),
      detail: `${byFreshnessP95[0]} ws`,
      tone: "good",
    },
    {
      label: "Best Arrival p95",
      value: displayForMetric(byArrivalP95[1].arrival_delta_ms.p95 ?? 0, "ms"),
      detail: `${byArrivalP95[0]} ws`,
      tone: "accent",
    },
    {
      label: "Smallest Gap",
      value: displayForMetric(byLargestGap[1].largest_relative_gap_ms ?? 0, "ms"),
      detail: `${byLargestGap[0]} ws`,
      tone: "accent",
    },
    {
      label: "Baseline",
      value: `${ci.baseline_topology} ws`,
      detail: "for comparison",
      tone: "neutral",
    },
  ];
}

export type HistogramBin = {
  start: number;
  end: number;
  count: number;
};

export type DistributionForChart = {
  topologySize: number;
  metric: "freshness" | "arrival";
  color: string;
  bins: HistogramBin[];
  distribution: Distribution | undefined;
};

export function topologyDistribution(
  summary: Summary,
  metric: "freshness" | "arrival",
  topologySize: number
): DistributionForChart {
  const t = summary.topologies[String(topologySize)];
  const histogram: Histogram | undefined =
    metric === "freshness"
      ? t?.freshness_histogram_ms
      : t?.arrival_delta_histogram_ms;
  const distribution =
    metric === "freshness" ? t?.freshness_ms : t?.arrival_delta_ms;
  return {
    topologySize,
    metric,
    color: topologyColor(topologySize),
    bins: histogram?.bins ?? [],
    distribution,
  };
}

export type ConnectionRow = {
  connectionId: string;
  topologySize: number;
  coverageRate: number;
  firstSeenWinRate: number;
  arrivalP50: number;
  arrivalP95: number;
  freshnessP50: number;
  freshnessP95: number;
  reconnects: number;
  disconnects: number;
  duplicateObservations: number;
  longestSilenceSeconds: number;
  lastError: string | null;
};

export function connectionRows(summary: Summary): ConnectionRow[] {
  return Object.values(summary.connections)
    .map((c: ConnectionStats) => ({
      connectionId: c.connection_id,
      topologySize: c.topology_size,
      coverageRate: c.coverage_rate,
      firstSeenWinRate: c.first_seen_win_rate,
      arrivalP50: c.arrival_delta_ms.p50 ?? 0,
      arrivalP95: c.arrival_delta_ms.p95 ?? 0,
      freshnessP50: c.freshness_ms.p50 ?? 0,
      freshnessP95: c.freshness_ms.p95 ?? 0,
      reconnects: c.reconnects ?? 0,
      disconnects: c.disconnects ?? 0,
      duplicateObservations: c.duplicate_observations ?? 0,
      longestSilenceSeconds: c.longest_silence_seconds ?? 0,
      lastError: c.last_error ?? null,
    }))
    .sort((a, b) => {
      if (a.topologySize !== b.topologySize)
        return a.topologySize - b.topologySize;
      return a.connectionId.localeCompare(b.connectionId);
    });
}

export function runDurationSeconds(summary: Summary): number {
  return summary.run_metadata.duration_seconds;
}

export type SegmentGapHit = {
  connectionId: string;
  topologySize: number;
  durationMs: number;
  events: number;
  startedAt: string;
  endedAt: string;
};

export type SegmentCell = {
  index: number;
  segmentId: string;
  marketSlug: string;
  startedAt: string;
  endedAt: string;
  startedAtMs: number;
  endedAtMs: number;
  durationSeconds: number;
  gapHits: SegmentGapHit[];
  hasSocketStall: boolean;
  hasTopo1Gap: boolean;
  worstGapMs: number;
};

export type MarketRotationTimeline = {
  segments: SegmentCell[];
  startedAtMs: number;
  endedAtMs: number;
  startedAtIso: string;
  endedAtIso: string;
  totalDurationSeconds: number;
  seriesId: string;
  marketFamily: string;
  markerIndices: number[];
};

function extractMarketFamily(slug: string): string {
  // "btc-updown-5m-1776832200" → "btc-updown-5m"
  const parts = slug.split("-");
  if (parts.length < 2) return slug;
  // drop trailing numeric tokens
  let end = parts.length;
  while (end > 1 && /^\d+$/.test(parts[end - 1])) end--;
  return parts.slice(0, end).join("-").toUpperCase();
}

export function marketRotationTimeline(
  summary: Summary
): MarketRotationTimeline | null {
  const segments = summary.market_segments ?? [];
  if (segments.length === 0) return null;

  const sorted = [...segments].sort((a, b) =>
    a.started_at_iso.localeCompare(b.started_at_iso)
  );

  const gapRecords: SegmentGapHit[] = Object.values(summary.connections)
    .map((c) => {
      const g = c.largest_relative_gap;
      if (!g) return null;
      return {
        connectionId: c.connection_id,
        topologySize: c.topology_size,
        durationMs: g.duration_ms,
        events: g.events,
        startedAt: g.started_at,
        endedAt: g.ended_at,
      } satisfies SegmentGapHit;
    })
    .filter((x): x is SegmentGapHit => x !== null);

  const cells: SegmentCell[] = sorted.map((seg, i) => {
    const start = Date.parse(seg.started_at_iso);
    const end = Date.parse(seg.ended_at_iso);
    const gapHits = gapRecords.filter((g) => {
      const gStart = Date.parse(g.startedAt);
      const gEnd = Date.parse(g.endedAt);
      // overlap if gap interval intersects segment interval
      return gEnd >= start && gStart <= end;
    });
    const hasTopo1Gap = gapHits.some((g) => g.topologySize === 1);
    const worstGapMs = gapHits.reduce(
      (max, g) => (g.durationMs > max ? g.durationMs : max),
      0
    );
    return {
      index: i,
      segmentId: seg.segment_id,
      marketSlug: seg.market_slug,
      startedAt: seg.started_at_iso,
      endedAt: seg.ended_at_iso,
      startedAtMs: start,
      endedAtMs: end,
      durationSeconds: Math.max(0, (end - start) / 1000),
      gapHits,
      hasSocketStall: gapHits.length > 0,
      hasTopo1Gap,
      worstGapMs,
    };
  });

  const firstMs = cells[0].startedAtMs;
  const lastMs = cells[cells.length - 1].endedAtMs;

  // Choose marker indices: first, last, every ~10th, and any flagged segment.
  const markerSet = new Set<number>();
  markerSet.add(0);
  markerSet.add(cells.length - 1);
  const step = cells.length <= 12 ? 1 : Math.max(1, Math.round(cells.length / 8));
  for (let i = 0; i < cells.length; i += step) markerSet.add(i);
  for (const cell of cells)
    if (cell.hasSocketStall) markerSet.add(cell.index);
  const markerIndices = Array.from(markerSet).sort((a, b) => a - b);

  return {
    segments: cells,
    startedAtMs: firstMs,
    endedAtMs: lastMs,
    startedAtIso: cells[0].startedAt,
    endedAtIso: cells[cells.length - 1].endedAt,
    totalDurationSeconds: Math.round((lastMs - firstMs) / 1000),
    seriesId: summary.run_metadata.series_id,
    marketFamily: extractMarketFamily(cells[0].marketSlug),
    markerIndices,
  };
}
