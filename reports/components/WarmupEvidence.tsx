import type { Summary } from "@/lib/summary-schema";
import { Card, SectionHeader } from "./primitives/Card";
import { Chip } from "./primitives/Chip";
import { fmtDelta, fmtMs } from "@/lib/format";
import { topologyColor } from "@/lib/palette";

export function WarmupEvidence({ summary }: { summary: Summary }) {
  const entries = Object.entries(summary.warmup_evidence.topology_comparison)
    .map(([k, v]) => [Number(k), v] as const)
    .sort((a, b) => a[0] - b[0]);

  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Warmup evidence"
        title="Does the warmup window actually matter?"
        subtitle="Compares freshness p95 (and arrival p95) during warmup vs the first stable window after reconnect. `supports_warmup` is true when post-warmup is meaningfully better."
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {entries.map(([size, w]) => (
          <Card key={size} className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div
                className="font-mono text-sm font-semibold"
                style={{ color: topologyColor(size) }}
              >
                {size} ws
              </div>
              <Chip tone={w.supports_warmup ? "good" : "warn"}>
                {w.supports_warmup ? "supports warmup" : "inconclusive"}
              </Chip>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <Col label="Warmup p95" value={fmtMs(w.warmup_freshness_p95_ms)} />
              <Col
                label="Post p95"
                value={fmtMs(w.post_warmup_freshness_p95_ms)}
              />
              <Col
                label="Δ p95"
                value={`${fmtDelta(w.freshness_p95_delta_ms)}ms`}
                tone={w.freshness_p95_delta_ms > 0 ? "good" : "bad"}
              />
            </div>
            <div className="text-xs text-text-subtle">
              Arrival p95: {fmtMs(w.warmup_arrival_p95_ms)} →{" "}
              {fmtMs(w.post_warmup_arrival_p95_ms)}{" "}
              <span
                className={
                  w.arrival_p95_delta_ms > 0 ? "text-good" : "text-bad"
                }
              >
                ({fmtDelta(w.arrival_p95_delta_ms)}ms)
              </span>
            </div>
          </Card>
        ))}
      </div>
    </section>
  );
}

function Col({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const tClass =
    tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 font-mono text-sm ${tClass}`}>{value}</div>
    </div>
  );
}
