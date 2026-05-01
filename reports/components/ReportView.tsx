import Link from "next/link";
import type { Route } from "next";
import type { Summary } from "@/lib/summary-schema";
import { Hero } from "./Hero";
import { KpiStrip } from "./KpiStrip";
import { AnswerCards } from "./AnswerCards";
import { MarketRotationTimeline } from "./MarketRotationTimeline";
import { TopologyTable } from "./TopologyTable";
import { TopologyChartsGrid } from "./charts/TopologyChartsGrid";
import { ConnectionOutliersChart } from "./charts/ConnectionOutliersChart";
import { DistributionChart } from "./charts/DistributionChart";
import { ConnectionsTable } from "./ConnectionsTable";
import { WarmupEvidence } from "./WarmupEvidence";
import { CaveatsPanel } from "./CaveatsPanel";
import { RunMetadata } from "./RunMetadata";

export function ReportView({
  summary,
  timestamp,
  hasTimeline,
}: {
  summary: Summary;
  timestamp?: string;
  hasTimeline?: boolean;
}) {
  return (
    <main>
      <Hero summary={summary} timestamp={timestamp} />
      {hasTimeline && timestamp ? (
        <div className="mb-6 flex items-center justify-between rounded-md border border-border bg-bg-surface px-4 py-3">
          <div>
            <div className="eyebrow text-accent">Per-socket view</div>
            <p className="mt-1 text-sm text-text">
              Inspect each connection on a wall-clock timeline — gaps,
              reconnects, and per-bucket lag-vs-leader.
            </p>
          </div>
          <Link
            href={`/report/${timestamp}/timeline` as Route}
            className="font-mono text-xs uppercase tracking-wider text-accent transition-colors hover:text-text"
          >
            Open timeline →
          </Link>
        </div>
      ) : null}
      <KpiStrip summary={summary} />
      <AnswerCards summary={summary} />
      <MarketRotationTimeline summary={summary} />
      <TopologyTable summary={summary} />
      <TopologyChartsGrid summary={summary} />
      <div className="mb-8 grid gap-3 lg:grid-cols-2">
        <ConnectionOutliersChart summary={summary} />
        <DistributionChart summary={summary} />
      </div>
      <ConnectionsTable summary={summary} />
      <WarmupEvidence summary={summary} />
      <CaveatsPanel summary={summary} />
      <RunMetadata summary={summary} />
    </main>
  );
}
