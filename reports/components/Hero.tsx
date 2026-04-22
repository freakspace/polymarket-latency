import type { Summary } from "@/lib/summary-schema";
import { fmtDuration, fmtTimestamp } from "@/lib/format";

export function Hero({
  summary,
  timestamp,
}: {
  summary: Summary;
  timestamp?: string;
}) {
  const md = summary.run_metadata;
  const markets = md.observed_market_slugs?.length
    ? md.observed_market_slugs
    : md.market_slug
      ? [md.market_slug]
      : [];

  return (
    <header className="mb-8 border-b border-border pb-8">
      <div className="eyebrow text-accent">Polymarket CLOB WS Benchmark</div>
      <h1 className="mt-2 text-[32px] font-bold leading-tight tracking-tight">
        Topology scaling report
      </h1>
      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-subtle">
        {markets.length > 0 && (
          <Meta label="Market" value={markets.join(", ")} mono />
        )}
        <Meta label="Series" value={md.series_id} mono />
        <Meta label="Topologies" value={md.topologies.join(", ")} />
        <Meta label="Duration" value={fmtDuration(md.duration_seconds)} />
        <Meta label="Started" value={fmtTimestamp(md.started_at)} mono />
        <Meta label="Ended" value={fmtTimestamp(md.ended_at)} mono />
      </div>
      {timestamp && (
        <div className="mt-3 font-mono text-xs text-text-subtle">
          run id: {timestamp}
        </div>
      )}
    </header>
  );
}

function Meta({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <span>
      <span className="text-text-subtle">{label}: </span>
      <span
        className={`font-semibold text-text ${mono ? "font-mono text-[13px]" : ""}`}
      >
        {value}
      </span>
    </span>
  );
}
