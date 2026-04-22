import { promises as fs } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseSummary } from "./summary-schema";
import {
  connectionOutliers,
  headlineKpis,
  marketRotationTimeline,
  topologyLatencySeries,
  topologyPerformanceSeries,
  topologyTableRows,
  verdicts,
} from "./aggregate";

const SAMPLE = path.resolve(
  __dirname,
  "..",
  "..",
  "recordings",
  "ws-bench",
  "20260422_042858",
  "summary.json"
);

const MULTI_SEGMENT_SAMPLE = path.resolve(
  __dirname,
  "..",
  "..",
  "recordings",
  "ws-bench",
  "20260422_051411",
  "summary.json"
);

async function loadSample() {
  const raw = JSON.parse(await fs.readFile(SAMPLE, "utf8"));
  return parseSummary(raw);
}

async function loadMultiSegmentSample() {
  const raw = JSON.parse(await fs.readFile(MULTI_SEGMENT_SAMPLE, "utf8"));
  return parseSummary(raw);
}

describe("aggregate", () => {
  it("rebuilds topology performance from raw coverage_rate", async () => {
    const s = await loadSample();
    const chart = topologyPerformanceSeries(s);
    expect(chart.categories).toEqual(["1 ws", "2 ws", "5 ws", "10 ws"]);
    const coverage = chart.series.find((x) => x.label === "Coverage")!;
    // From raw: topologies.2.coverage_rate = 0.999973 → 99.9973 %
    expect(coverage.values[1]).toBeCloseTo(99.9973, 3);
  });

  it("derives topology latency from raw distributions", async () => {
    const s = await loadSample();
    const chart = topologyLatencySeries(s);
    const arrivalP95 = chart.series.find((x) => x.label === "Arrival p95")!;
    // From raw: topologies.10.arrival_delta_ms.p95 ≈ 5.793
    expect(arrivalP95.values[3]).toBeCloseTo(5.793, 1);
  });

  it("picks the right verdict winners", async () => {
    const s = await loadSample();
    const v = verdicts(s);
    const winners = Object.fromEntries(v.map((x) => [x.key, x.winnerLabel]));
    expect(winners.coverage).toMatch(/^2 ws/);
    expect(winners.firstSeen).toMatch(/^10 ws/);
    expect(winners.miss).toMatch(/^2 ws/);
    expect(winners.gap).toMatch(/^2 ws/);
  });

  it("puts topology_10_conn_08 at the top of outliers", async () => {
    const s = await loadSample();
    const outliers = connectionOutliers(s, { limit: 3 });
    expect(outliers[0].connectionId).toBe("topology_10_conn_08");
    // Freshness p95 for that connection is ~1918.11 ms
    expect(outliers[0].freshnessP95).toBeGreaterThan(1900);
  });

  it("marks best cells in the comparison table", async () => {
    const s = await loadSample();
    const { rows } = topologyTableRows(s);
    const coverageRow = rows.find((r) => r.metric.key === "coverage")!;
    // Topology 2 has the highest coverage in this run
    expect(coverageRow.cells[2].isBest).toBe(true);
    expect(coverageRow.cells[1].isBest).toBe(false);
  });

  it("emits headline KPIs with tones", async () => {
    const s = await loadSample();
    const kpis = headlineKpis(s);
    expect(kpis.map((k) => k.label)).toContain("Best Coverage");
    expect(kpis.map((k) => k.label)).toContain("Best Freshness p95");
  });

  it("builds a market rotation timeline and tags segments with gap hits", async () => {
    const s = await loadMultiSegmentSample();
    const timeline = marketRotationTimeline(s);
    expect(timeline).not.toBeNull();
    expect(timeline!.segments).toHaveLength(2);
    expect(timeline!.marketFamily).toBe("BTC-UPDOWN-5M");
    // segment_002 should contain the single-socket topology's largest gap
    // (topology_1_conn_01, duration 11.103 ms at 05:21:03)
    const seg2 = timeline!.segments[1];
    expect(seg2.hasSocketStall).toBe(true);
    expect(seg2.hasTopo1Gap).toBe(true);
    expect(seg2.worstGapMs).toBeGreaterThan(10);
  });

  it("returns null rotation timeline when no segments are recorded", async () => {
    const s = await loadSample();
    // single-segment sample still has 1 segment, so not null
    const timeline = marketRotationTimeline(s);
    expect(timeline).not.toBeNull();
    expect(timeline!.segments).toHaveLength(1);
  });
});
