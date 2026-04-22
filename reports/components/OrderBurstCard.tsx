import Link from "next/link";
import type { OrderBurstListing } from "@/lib/order-burst";
import { headlineByFanout } from "@/lib/order-burst";
import { fmtMs, fmtPercent } from "@/lib/format";

export function OrderBurstCard({ run }: { run: OrderBurstListing }) {
  const headlines = headlineByFanout(run.summary);
  const largest = headlines[headlines.length - 1];
  const one = headlines.find((h) => h.fanout === 1);
  const successRateAll = computeOverallSuccess(run);

  return (
    <Link
      href={`/order-burst/${run.timestamp}` as never}
      className="group card card-pad flex flex-col gap-3 transition-colors hover:border-border-strong hover:bg-bg-elevated"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-text-subtle">
            {run.timestamp}
          </div>
          <div className="mt-0.5 text-sm font-semibold text-text">
            {run.summary.side} · {run.summary.price} × {run.summary.size}
          </div>
        </div>
        <div className="text-right">
          <div className="eyebrow">Fanouts</div>
          <div className="mt-0.5 font-mono text-sm text-text">
            {run.summary.counts.join(", ")}
          </div>
        </div>
      </header>

      <div className="font-mono text-xs text-text-muted">
        <div className="truncate">
          <span className="text-text-subtle">token: </span>
          {run.summary.token_id}
        </div>
        <div>
          <span className="text-text-subtle">host: </span>
          {run.summary.host.replace(/^https?:\/\//, "")}
          <span className="mx-2 text-text-subtle">·</span>
          <span className="text-text-subtle">chain: </span>
          {run.summary.chain_id}
        </div>
      </div>

      <div className="mt-1 grid grid-cols-3 gap-2">
        <div>
          <div className="eyebrow truncate">Success</div>
          <div className="mt-0.5 font-mono text-[13px] font-semibold text-text">
            {fmtPercent(successRateAll, 0)}
          </div>
          <div className="text-xxs text-text-subtle">
            across all fanouts
          </div>
        </div>
        <div>
          <div className="eyebrow truncate">Fanout=1 fastest</div>
          <div className="mt-0.5 font-mono text-[13px] font-semibold text-text">
            {fmtMs(one?.fastestMs ?? undefined)}
          </div>
          <div className="text-xxs text-text-subtle">baseline</div>
        </div>
        <div>
          <div className="eyebrow truncate">
            Fanout={largest?.fanout ?? "?"} fastest
          </div>
          <div className="mt-0.5 font-mono text-[13px] font-semibold text-text">
            {fmtMs(largest?.fastestMs ?? undefined)}
          </div>
          <div className="text-xxs text-text-subtle">
            {largest?.improvementVsOneMs != null
              ? `${largest.improvementVsOneMs > 0 ? "−" : "+"}${Math.abs(largest.improvementVsOneMs).toFixed(1)}ms vs 1`
              : "—"}
          </div>
        </div>
      </div>

      <div className="mt-1 text-xxs text-accent opacity-0 transition-opacity group-hover:opacity-100">
        Open report →
      </div>
    </Link>
  );
}

function computeOverallSuccess(run: OrderBurstListing): number {
  let success = 0;
  let total = 0;
  for (const r of run.summary.results) {
    total += r.requests.length;
    success += r.requests.filter((req) => req.kind === "success").length;
  }
  return total === 0 ? 0 : success / total;
}
