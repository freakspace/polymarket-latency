import type { Summary } from "@/lib/summary-schema";
import { verdicts } from "@/lib/aggregate";
import { Card, SectionHeader } from "./primitives/Card";
import { Chip } from "./primitives/Chip";

const CHIP_LABELS: Record<"yes" | "mixed" | "no", string> = {
  yes: "Yes",
  mixed: "Mixed",
  no: "No",
};

const CHIP_TONES: Record<"yes" | "mixed" | "no", "good" | "warn" | "bad"> = {
  yes: "good",
  mixed: "warn",
  no: "bad",
};

export function AnswerCards({ summary }: { summary: Summary }) {
  const items = verdicts(summary);
  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Verdicts"
        title="Does scaling the websocket pool actually help?"
        subtitle="Four questions, four answers, derived from the per-topology metrics and compared against the baseline."
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((v) => (
          <Card key={v.key} className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-[15px] font-semibold leading-snug">
                {v.title}
              </h3>
              <Chip tone={CHIP_TONES[v.chip]}>{CHIP_LABELS[v.chip]}</Chip>
            </div>
            <div className="font-mono text-sm text-accent">
              {v.winnerLabel}
            </div>
            <p className="text-[13px] leading-relaxed text-text-muted">
              {v.body}
            </p>
          </Card>
        ))}
      </div>
    </section>
  );
}
