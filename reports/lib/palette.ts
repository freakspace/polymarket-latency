export const TOPOLOGY_COLORS: Record<number, string> = {
  1: "#2563eb",
  2: "#059669",
  5: "#7c3aed",
  10: "#db2777",
};

export const METRIC_COLORS = {
  coverage: "#2563eb",
  firstSeen: "#059669",
  miss: "#dc2626",
  arrivalP50: "#2563eb",
  arrivalP95: "#059669",
  freshnessP50: "#7c3aed",
  freshnessP95: "#ea580c",
  gapRuns: "#2563eb",
  largestGap: "#dc2626",
  relLoss: "#ea580c",
  gapMs: "#2563eb",
  gapDurP95: "#059669",
  interEventP95: "#7c3aed",
  warmupP95: "#dc2626",
  postP95: "#059669",
  delta: "#ea580c",
} as const;

export const SEMANTIC = {
  good: "#059669",
  warn: "#b45309",
  bad: "#dc2626",
  accent: "#2563eb",
} as const;

export const SEGMENT_COLORS = {
  normal: "#c2410c",
  stall: "#f59e0b",
  topo1: "#7c3aed",
} as const;

export function topologyColor(size: number | string): string {
  const n = typeof size === "string" ? Number(size) : size;
  return TOPOLOGY_COLORS[n] ?? SEMANTIC.accent;
}
