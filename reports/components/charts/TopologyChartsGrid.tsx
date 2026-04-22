import type { Summary } from "@/lib/summary-schema";
import {
  topologyGapCountsSeries,
  topologyGapDurationSeries,
  topologyLatencySeries,
  topologyPerformanceSeries,
  warmupDeltaSeries,
} from "@/lib/aggregate";
import { ChartCard } from "./ChartCard";
import { GroupedBarChart } from "./GroupedBarChart";
import { SectionHeader } from "../primitives/Card";

export function TopologyChartsGrid({ summary }: { summary: Summary }) {
  const performance = topologyPerformanceSeries(summary);
  const latency = topologyLatencySeries(summary);
  const gapCounts = topologyGapCountsSeries(summary);
  const gapDuration = topologyGapDurationSeries(summary);
  const warmup = warmupDeltaSeries(summary);

  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Charts"
        title="Topology comparisons"
        subtitle="Each chart is rebuilt from raw per-topology fields — not from the pre-shaped visualization_data block."
      />
      <div className="grid gap-3 lg:grid-cols-2">
        <ChartCard title={performance.title} subtitle={performance.subtitle}>
          <GroupedBarChart payload={performance} />
        </ChartCard>
        <ChartCard title={latency.title} subtitle={latency.subtitle}>
          <GroupedBarChart payload={latency} />
        </ChartCard>
        <ChartCard title={gapCounts.title} subtitle={gapCounts.subtitle}>
          <GroupedBarChart payload={gapCounts} />
        </ChartCard>
        <ChartCard title={gapDuration.title} subtitle={gapDuration.subtitle}>
          <GroupedBarChart payload={gapDuration} />
        </ChartCard>
        <ChartCard title={warmup.title} subtitle={warmup.subtitle}>
          <GroupedBarChart payload={warmup} />
        </ChartCard>
      </div>
    </section>
  );
}
