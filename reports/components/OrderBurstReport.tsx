import type { OrderBurstListing, OrderBurstRequest } from "@/lib/order-burst";
import { aggregateByFanout, repeatRuns } from "@/lib/order-burst";
import { fmtMs, fmtPercent } from "@/lib/format";

export function OrderBurstReport({ run }: { run: OrderBurstListing }) {
  const s = run.summary;
  const repeatRunsList = repeatRuns(s);
  const aggregateRows = aggregateByFanout(s);
  const baselineRow = aggregateRows.find((row) => row.fanout === 1);

  const totalReqs = repeatRunsList.reduce(
    (sum, repeatRun) =>
      sum +
      repeatRun.results.reduce((inner, r) => inner + r.requests.length, 0),
    0
  );
  const totalSuccess = repeatRunsList.reduce(
    (sum, repeatRun) =>
      sum +
      repeatRun.results.reduce(
        (inner, r) =>
          inner +
          (r.client_success_count ??
            r.requests.filter((q) => q.kind === "success").length),
        0
      ),
    0
  );
  const totalFanoutRuns = repeatRunsList.reduce(
    (sum, repeatRun) => sum + repeatRun.results.length,
    0
  );
  const totalWinnersLanded = repeatRunsList.reduce(
    (sum, repeatRun) =>
      sum +
      repeatRun.results.reduce(
        (inner, r) => inner + ((r.winner_landed ?? r.new_open_order_count > 0) ? 1 : 0),
        0
      ),
    0
  );
  const totalLandedWithoutSuccess = repeatRunsList.reduce(
    (sum, repeatRun) =>
      sum +
      repeatRun.results.reduce((inner, r) => {
        const clientSuccessCount =
          r.client_success_count ??
          r.requests.filter((q) => q.kind === "success").length;
        const landedWithoutSuccess =
          r.landed_without_success_response ??
          (r.new_open_order_count > 0 && clientSuccessCount < r.new_open_order_count);
        return inner + (landedWithoutSuccess ? 1 : 0);
      }, 0),
    0
  );
  const totalErrors = totalReqs - totalSuccess;
  const totalOrdersOnBook = repeatRunsList.reduce(
    (sum, repeatRun) =>
      sum + repeatRun.results.reduce((inner, r) => inner + r.new_open_order_count, 0),
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

      <section className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Kpi label="Repeats" value={String(s.repeats ?? repeatRunsList.length)} />
        <Kpi label="Requests" value={totalReqs.toLocaleString()} />
        <Kpi
          label="Client Success"
          value={`${totalSuccess}/${totalReqs}`}
          detail={fmtPercent(totalReqs ? totalSuccess / totalReqs : 0, 0)}
        />
        <Kpi
          label="Winner Landed"
          value={`${totalWinnersLanded}/${totalFanoutRuns}`}
          detail="fanouts with at least one order on book"
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
        <Kpi
          label="Landed Without Success"
          value={totalLandedWithoutSuccess.toString()}
          detail="winner landed, but client saw no matching success"
        />
      </section>

      <section className="card card-pad">
        <div className="eyebrow mb-2">Per-fanout summary</div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-sm">
            <thead>
              <tr className="text-left text-xxs uppercase tracking-wider text-text-subtle">
                <th className="py-2 pr-4">Fanout</th>
                <th className="py-2 pr-4">Repeats</th>
                <th className="py-2 pr-4">Winner landed</th>
                <th className="py-2 pr-4">Client success</th>
                <th className="py-2 pr-4">Winner latency</th>
                <th className="py-2 pr-4">Range</th>
                <th className="py-2 pr-4">Median Δ vs 1</th>
                <th className="py-2 pr-4">Beat 1</th>
                <th className="py-2 pr-4">Dupes</th>
                <th className="py-2 pr-4">Transport</th>
                <th className="py-2 pr-4">Landed w/o success</th>
                <th className="py-2 pr-4">On book</th>
              </tr>
            </thead>
            <tbody className="font-mono text-[13px]">
              {aggregateRows.map((row) => {
                return (
                  <tr
                    key={row.fanout}
                    className="border-t border-border/60 text-text"
                  >
                    <td className="py-2 pr-4">{row.fanout}</td>
                    <td className="py-2 pr-4">{row.repeat_count}</td>
                    <td className="py-2 pr-4">
                      {row.winner_landed_count}/{row.repeat_count}{" "}
                      <span className="text-text-subtle">
                        ({fmtPercent(row.winner_landed_rate, 0)})
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {row.client_success_repeat_count}/{row.repeat_count}{" "}
                      <span className="text-text-subtle">
                        ({fmtPercent(row.client_success_rate, 0)})
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {fmtMs(row.observed_winner_latency_ms.median ?? undefined)}
                    </td>
                    <td className="py-2 pr-4">
                      {fmtMs(row.observed_winner_latency_ms.min ?? undefined)}{" "}
                      <span className="text-text-subtle">/</span>{" "}
                      {fmtMs(row.observed_winner_latency_ms.max ?? undefined)}
                    </td>
                    <td className="py-2 pr-4">
                      {row.improvement_vs_repeat_baseline_ms.median == null
                        ? "—"
                        : row.fanout === 1
                          ? "baseline"
                          : `${row.improvement_vs_repeat_baseline_ms.median >= 0 ? "−" : "+"}${Math.abs(row.improvement_vs_repeat_baseline_ms.median).toFixed(1)}ms`}
                    </td>
                    <td className="py-2 pr-4">
                      {row.beat_repeat_baseline_count}/{row.comparable_repeat_count}
                    </td>
                    <td className="py-2 pr-4">{row.duplicate_reject_total}</td>
                    <td className="py-2 pr-4">{row.transport_error_total}</td>
                    <td className="py-2 pr-4">
                      {row.landed_without_success_response_count}/{row.repeat_count}
                    </td>
                    <td className="py-2 pr-4">{row.orders_landed_total}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {baselineRow?.observed_winner_latency_ms.median != null && (
          <div className="mt-3 text-xxs text-text-subtle">
            Winner latency uses the fastest successful client response seen in a
            repeat. Δ is measured against that same repeat’s fanout=1 baseline.
          </div>
        )}
      </section>

      {repeatRunsList.map((repeatRun) => (
        <section key={repeatRun.repeat_index} className="flex flex-col gap-4">
          {repeatRunsList.length > 1 && (
            <div className="eyebrow">
              repeat {repeatRun.repeat_index}/{repeatRunsList.length}
            </div>
          )}
          {repeatRun.results.map((r) => (
            <FanoutDetail
              key={`${repeatRun.repeat_index}-${r.fanout}`}
              result={r}
            />
          ))}
        </section>
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
  const clientSuccessCount =
    result.client_success_count ??
    result.requests.filter((req) => req.kind === "success").length;
  const winnerLanded = result.winner_landed ?? result.new_open_order_count > 0;
  const landedWithoutSuccessResponse =
    result.landed_without_success_response ??
    (result.new_open_order_count > 0 &&
      clientSuccessCount < result.new_open_order_count);
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
          client success: {clientSuccessCount}/{result.requests.length}
          <span className="mx-2 text-text-subtle">·</span>
          winner landed: {winnerLanded ? "yes" : "no"}
          <span className="mx-2 text-text-subtle">·</span>
          landed w/o success: {landedWithoutSuccessResponse ? "yes" : "no"}
          <span className="mx-2 text-text-subtle">·</span>
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
