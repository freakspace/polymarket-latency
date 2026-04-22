import type { Summary } from "@/lib/summary-schema";
import { Card, SectionHeader } from "./primitives/Card";

export function CaveatsPanel({ summary }: { summary: Summary }) {
  const caveats = summary.caveats ?? [];
  if (caveats.length === 0) return null;
  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Read carefully"
        title="Caveats"
        subtitle="Things to keep in mind when interpreting these numbers."
      />
      <Card>
        <ul className="list-inside list-disc space-y-1.5 text-sm text-text-muted">
          {caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
