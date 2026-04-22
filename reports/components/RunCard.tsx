import Link from "next/link";
import type { RunListing } from "@/lib/recordings";
import { fmtDuration, fmtTimestamp } from "@/lib/format";
import { headlineKpis } from "@/lib/aggregate";

export function RunCard({ run }: { run: RunListing }) {
  const md = run.summary.run_metadata;
  const kpis = headlineKpis(run.summary).slice(0, 3);
  const market =
    md.observed_market_slugs?.[0] ?? md.market_slug ?? "(no market)";

  return (
    <Link
      href={`/report/${run.timestamp}` as never}
      className="group card card-pad flex flex-col gap-3 transition-colors hover:border-border-strong hover:bg-bg-elevated"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-text-subtle">
            {run.timestamp}
          </div>
          <div className="mt-0.5 text-sm font-semibold text-text">
            {fmtTimestamp(md.started_at)}
          </div>
        </div>
        <div className="text-right">
          <div className="eyebrow">Duration</div>
          <div className="mt-0.5 font-mono text-sm text-text">
            {fmtDuration(md.duration_seconds)}
          </div>
        </div>
      </header>

      <div className="font-mono text-xs text-text-muted">
        <div>
          <span className="text-text-subtle">market: </span>
          {market}
        </div>
        <div>
          <span className="text-text-subtle">series: </span>
          {md.series_id}
          <span className="mx-2 text-text-subtle">·</span>
          <span className="text-text-subtle">topologies: </span>
          {md.topologies.join(", ")}
        </div>
      </div>

      <div className="mt-1 grid grid-cols-3 gap-2">
        {kpis.map((k) => (
          <div key={k.label}>
            <div className="eyebrow truncate">{k.label}</div>
            <div className="mt-0.5 font-mono text-[13px] font-semibold text-text">
              {k.value}
            </div>
            {k.detail && (
              <div className="text-xxs text-text-subtle">{k.detail}</div>
            )}
          </div>
        ))}
      </div>

      <div className="mt-1 text-xxs text-accent opacity-0 transition-opacity group-hover:opacity-100">
        Open report →
      </div>
    </Link>
  );
}
