import type { Summary } from "@/lib/summary-schema";
import { headlineKpis } from "@/lib/aggregate";
import { StatTile } from "./primitives/StatTile";

export function KpiStrip({ summary }: { summary: Summary }) {
  const kpis = headlineKpis(summary);
  return (
    <section className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      {kpis.map((kpi) => (
        <StatTile
          key={kpi.label}
          label={kpi.label}
          value={kpi.value}
          detail={kpi.detail}
          tone={kpi.tone}
        />
      ))}
    </section>
  );
}
