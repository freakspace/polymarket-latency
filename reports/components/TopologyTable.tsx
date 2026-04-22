import type { Summary } from "@/lib/summary-schema";
import { topologyTableRows } from "@/lib/aggregate";
import { Card, SectionHeader } from "./primitives/Card";
import { topologyColor } from "@/lib/palette";

export function TopologyTable({ summary }: { summary: Summary }) {
  const { sizes, rows } = topologyTableRows(summary);
  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Comparison"
        title="Per-topology metrics"
        subtitle="Best value per row is highlighted. Arrow direction indicates whether lower or higher is better."
      />
      <Card className="!p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="border-b border-border">
                <th className="p-3 pl-5 text-left text-xxs font-semibold uppercase tracking-wider text-text-subtle">
                  Metric
                </th>
                {sizes.map((s) => (
                  <th
                    key={s}
                    className="p-3 text-right text-xxs font-semibold uppercase tracking-wider"
                  >
                    <span
                      className="font-mono"
                      style={{ color: topologyColor(s) }}
                    >
                      {s} ws
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ metric, cells }) => (
                <tr
                  key={metric.key}
                  className="border-b border-border last:border-0"
                >
                  <td className="p-3 pl-5 font-medium text-text-muted">
                    <span className="flex items-center gap-2">
                      {metric.label}
                      <span className="text-xxs text-text-subtle">
                        {metric.direction === "low" ? "↓" : "↑"}
                      </span>
                    </span>
                  </td>
                  {sizes.map((s) => {
                    const cell = cells[s];
                    return (
                      <td
                        key={s}
                        className={
                          "p-3 text-right font-mono text-[13px] " +
                          (cell.isBest
                            ? "font-semibold text-good"
                            : "text-text")
                        }
                      >
                        {cell.display}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </section>
  );
}
