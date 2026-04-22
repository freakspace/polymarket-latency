import type { OrderBurstListing, OrderBurstRequest } from "@/lib/order-burst";
import { headlineByFanout } from "@/lib/order-burst";
import { fmtMs, fmtPercent } from "@/lib/format";

export function OrderBurstReport({ run }: { run: OrderBurstListing }) {
  const s = run.summary;
  const headlines = headlineByFanout(s);
  const baselineFastest = headlines.find((h) => h.fanout === 1)?.fastestMs;

  const totalReqs = s.results.reduce((a, r) => a + r.requests.length, 0);
  const totalSuccess = s.results.reduce(
    (a, r) => a + r.requests.filter((q) => q.kind === "success").length,
    0
  );
  const totalErrors = totalReqs - totalSuccess;
  const totalOrdersOnBook = s.results.reduce(
    (a, r) => a + r.new_open_order_count,
    0
  );

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2 border-b border-border pb-6">
        <div className="eyebrow text-accent">Order Burst</div>
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-[28px] font-bold leading-tight tracking-tight">
            {run.timestamp}
          </h1>
          <span className="font-mono text-xs text-text-subtle">
            {s.side} · price {s.price} · size {s.size} · post_only=
            {String(s.post_only)} · cleanup={String(s.cleanup)}
          </span>
        </div>
        <div className="font-mono text-xs text-text-muted">
          <div className="truncate">
            <span className="text-text-subtle">token: </span>
            {s.token_id}
          </div>
          <div>
            <span className="text-text-subtle">host: </span>
            {s.host}
            <span className="mx-2 text-text-subtle">·</span>
            <span className="text-text-subtle">chain_id: </span>
            {s.chain_id}
            {s.resolved_chain_id !== s.chain_id && (
              <span className="ml-1 text-warning">
                (resolved {s.resolved_chain_id})
              </span>
            )}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Requests" value={totalReqs.toLocaleString()} />
        <Kpi
          label="Success"
          value={`${totalSuccess}/${totalReqs}`}
          detail={fmtPercent(totalReqs ? totalSuccess / totalReqs : 0, 0)}
        />
        <Kpi
          label="Errors"
          value={totalErrors.toString()}
          detail={totalErrors === 0 ? "clean" : "see per-fanout table"}
        />
        <Kpi
          label="Orders landed"
          value={totalOrdersOnBook.toString()}
          detail="seen on book post-burst"
        />
      </section>

      <section className="card card-pad">
        <div className="eyebrow mb-2">Per-fanout summary</div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="text-left text-xxs uppercase tracking-wider text-text-subtle">
                <th className="py-2 pr-4">Fanout</th>
                <th className="py-2 pr-4">Success</th>
                <th className="py-2 pr-4">Fastest success</th>
                <th className="py-2 pr-4">Median</th>
                <th className="py-2 pr-4">Min / Max</th>
                <th className="py-2 pr-4">Δ vs fanout=1</th>
                <th className="py-2 pr-4">On book</th>
              </tr>
            </thead>
            <tbody className="font-mono text-[13px]">
              {headlines.map((h) => {
                const res = s.results.find((r) => r.fanout === h.fanout);
                const newOnBook = res?.new_open_order_count ?? 0;
                return (
                  <tr
                    key={h.fanout}
                    className="border-t border-border/60 text-text"
                  >
                    <td className="py-2 pr-4">{h.fanout}</td>
                    <td className="py-2 pr-4">
                      {h.successCount}/{h.totalCount}{" "}
                      <span className="text-text-subtle">
                        ({fmtPercent(h.successRate, 0)})
                      </span>
                    </td>
                    <td className="py-2 pr-4">{fmtMs(h.fastestMs ?? undefined)}</td>
                    <td className="py-2 pr-4">{fmtMs(h.medianMs)}</td>
                    <td className="py-2 pr-4">
                      {fmtMs(h.minMs)}{" "}
                      <span className="text-text-subtle">/</span>{" "}
                      {fmtMs(h.maxMs)}
                    </td>
                    <td className="py-2 pr-4">
                      {h.improvementVsOneMs == null
                        ? "—"
                        : h.fanout === 1
                          ? "baseline"
                          : `${h.improvementVsOneMs >= 0 ? "−" : "+"}${Math.abs(h.improvementVsOneMs).toFixed(1)}ms`}
                    </td>
                    <td className="py-2 pr-4">{newOnBook}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {baselineFastest != null && (
          <div className="mt-3 text-xxs text-text-subtle">
            Baseline is the fastest successful response at fanout=1
            ({fmtMs(baselineFastest)}). Negative Δ means the fanout beat the
            baseline.
          </div>
        )}
      </section>

      {s.results.map((r) => (
        <FanoutDetail key={r.fanout} result={r} />
      ))}
    </div>
  );
}

function Kpi({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="card card-pad">
      <div className="eyebrow truncate">{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold text-text">
        {value}
      </div>
      {detail && (
        <div className="mt-0.5 text-xxs text-text-subtle">{detail}</div>
      )}
    </div>
  );
}

function FanoutDetail({
  result,
}: {
  result: OrderBurstListing["summary"]["results"][number];
}) {
  const requests = [...result.requests].sort((a, b) => a.index - b.index);
  const counts = result.summary.request_counts;
  return (
    <section className="card card-pad">
      <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="eyebrow">
            fanout = {result.fanout}
            <span className="ml-2 text-text-subtle">
              shared_ts {result.shared_timestamp_ms}
            </span>
          </div>
          <div className="mt-0.5 font-mono text-xs text-text-muted">
            {Object.entries(counts)
              .sort()
              .map(([k, v]) => `${k}=${v}`)
              .join(" · ")}
          </div>
        </div>
        <div className="font-mono text-xs text-text-muted">
          on book: {result.new_open_order_count}/{result.requests.length}
        </div>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] text-xs">
          <thead>
            <tr className="text-left text-xxs uppercase tracking-wider text-text-subtle">
              <th className="py-1.5 pr-3">#</th>
              <th className="py-1.5 pr-3">Latency</th>
              <th className="py-1.5 pr-3">Kind</th>
              <th className="py-1.5 pr-3">Status</th>
              <th className="py-1.5 pr-3">Order ID</th>
              <th className="py-1.5 pr-3">Error</th>
            </tr>
          </thead>
          <tbody className="font-mono text-text">
            {requests.map((req) => (
              <RequestRow key={req.index} req={req} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RequestRow({ req }: { req: OrderBurstRequest }) {
  return (
    <tr className="border-t border-border/60 align-top">
      <td className="py-1.5 pr-3 text-text-subtle">#{req.index}</td>
      <td className="py-1.5 pr-3">{fmtMs(req.latency_ms)}</td>
      <td className="py-1.5 pr-3">
        <KindBadge kind={req.kind} />
      </td>
      <td className="py-1.5 pr-3">{req.status ?? "—"}</td>
      <td className="py-1.5 pr-3 max-w-[280px] truncate">
        {req.order_id ?? "—"}
      </td>
      <td className="py-1.5 pr-3 text-warning">{req.error || ""}</td>
    </tr>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const color =
    kind === "success"
      ? "bg-emerald-500/15 text-emerald-400"
      : kind === "duplicate"
        ? "bg-amber-500/15 text-amber-400"
        : kind === "error"
          ? "bg-rose-500/15 text-rose-400"
          : "bg-border/40 text-text-subtle";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xxs uppercase ${color}`}>
      {kind}
    </span>
  );
}
