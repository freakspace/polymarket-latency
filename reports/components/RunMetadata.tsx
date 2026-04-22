import type { Summary } from "@/lib/summary-schema";
import { Card, SectionHeader } from "./primitives/Card";

export function RunMetadata({ summary }: { summary: Summary }) {
  const md = summary.run_metadata as Record<string, unknown>;
  const entries = Object.entries(md).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Raw config"
        title="Run metadata"
        subtitle="Everything under run_metadata in the source summary.json, rendered verbatim."
      />
      <Card className="!p-0">
        <details>
          <summary className="cursor-pointer select-none p-5 text-sm text-text-muted hover:bg-bg-muted">
            Show full run_metadata ({entries.length} fields)
          </summary>
          <div className="border-t border-border p-5">
            <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-text-muted">
              {JSON.stringify(md, null, 2)}
            </pre>
          </div>
        </details>
      </Card>
    </section>
  );
}
